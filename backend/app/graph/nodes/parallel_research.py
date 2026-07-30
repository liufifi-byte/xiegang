"""并行调研节点：5 议员同时独立调研，互不可见。

关键规则：议员之间彼此不可见——保证立场纯粹性。
每个议员：
  1. 读自己的 system prompt 模板
  2. 调自己的工具集（最多 2 次，MVP 限制）
  3. 用 GLM-5 生成结构化立场（stance / evidence / reasoning）

V1-S2: 异步化为 asyncio.gather，5 议员并发调研
      （从串行 60s 降到并发 20-25s）
"""
import asyncio
import time
from pathlib import Path

from app.agents.harness import AgentBudget
from app.agents.profiles import get_expert_profile
from app.agents.runner import ExpertResearchAgentRunner
from app.agents.schemas import ExpertResearchResult
from app.agents.tool_registry import ParliamentToolCache, ToolRegistry
from app.agents.trace import AgentTraceSink
from app.config import get_settings

from app.llm import get_structured_llm
from app.llm_concurrency import get_llm_semaphore
from app.schemas.member import MemberStatement
from app.tools.topic_specific import (
    contrarian_search,
    data_table,
    historical_risk_lookup,
    market_window_check,
    trend_research,
)
from app.tools.web_search import web_search


# 5 议员各配 2 个工具（web_search + 1 个特色）
MEMBER_TOOLS: dict[str, list] = {
    "suan": [web_search, data_table],
    "chen": [web_search, historical_risk_lookup],
    "ji":   [web_search, market_window_check],
    "nian": [web_search, trend_research],
    "fan":  [web_search, contrarian_search],
}

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# MVP 阶段：每个议员最多调几次工具
MAX_TOOL_CALLS_PER_MEMBER = 2


def _load_prompt_template(member_id: str) -> str:
    return (PROMPTS_DIR / f"{member_id}.md").read_text(encoding="utf-8")


def _sync_tool_search(tool, query: str) -> list[dict]:
    """工具调用都是同步函数（web_search / calculator 等），同步跑就行。

    LangChain ChatOpenAI 的同步工具调用不会阻塞其他 asyncio 协程，
    因为 `_research_for_member_async` 在 LLM 调用环节就让出控制权了。
    """
    try:
        return tool(query)
    except Exception as e:
        return [{"error": str(e)}]


async def _research_for_member_async(
    member_id: str, topic: str, variables: list[str],
    parliament_id: str | None = None,
    memories: str = "（暂无相关历史记忆）",
) -> MemberStatement:
    """V2-S3: 接受预计算的 memories 文本，注入 prompt 第 5 层。"""
    tools = MEMBER_TOOLS[member_id]
    tool_results: list[str] = []
    # P0-tools: 真实记录每个工具调用
    tool_call_records: list[dict] = []

    query_str = f"{topic} {' '.join(variables[:3])}"
    for tool in tools[:MAX_TOOL_CALLS_PER_MEMBER]:
        tool_name = getattr(tool, "__name__", str(tool))
        import time as _t
        call_start = _t.time()
        try:
            results = tool(query_str)
            result_count = len(results) if isinstance(results, list) else 0
            tool_call_records.append({
                "tool": tool_name,
                "query": query_str[:100],
                "result_count": result_count,
                "ok": True,
                "duration_ms": int((_t.time() - call_start) * 1000),
            })
            for r in results[:2]:
                tool_results.append(
                    f"- [{r.get('title', '')}]({r.get('url', '')}) "
                    f"{r.get('content', '')[:200]}"
                )
        except Exception as e:
            tool_call_records.append({
                "tool": tool_name,
                "query": query_str[:100],
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "duration_ms": int((_t.time() - call_start) * 1000),
            })
            tool_results.append(f"- [TOOL ERROR] {tool_name}: {e}")

    tool_context = "\n".join(tool_results) if tool_results else "（无工具结果）"

    template = _load_prompt_template(member_id)
    prompt = template.format(
        topic=topic,
        variables=", ".join(variables) if variables else "（用户未提供）",
        memories=memories,
    )

    # 在 prompt 末尾追加调研结果 + 输出指令
    prompt += f"""

## 当前调研输入
【用户议题】{topic}
【关键变量】{', '.join(variables) if variables else '（无）'}

## 工具检索结果
{tool_context}

## 你的输出
基于以上调研，给出立场 + 3 条核心证据。

严格 JSON 输出（不要其他文字、不要 markdown 代码块）：
{{
  "member_id": "{member_id}",
  "stance": "for|against|neutral",
  "confidence": 0.0-1.0,
  "evidence": [{{"claim": "...", "source": "...", "strength": "high|medium|low"}}],
  "reasoning_summary": "..."
}}"""

    structured = get_structured_llm(MemberStatement, temperature=0.6)

    # V2-S2: 真异步路径（去掉 to_thread），配合 cancel_guard 硬中断。
    # 限速通过 semaphore 控制（V1-S2 已有，保留）。
    sem = get_llm_semaphore()
    async with sem:
        # V2-S2: cancel_guard 包装
        from app.cancel_guard import guard_ainvoke
        stmt = await guard_ainvoke(
            parliament_id=parliament_id or "_no_parliament",
            coro=structured.ainvoke(prompt),
            timeout=120,  # 单 LLM 调用硬上限 120s（含 web search 耗时）
        )
    # 把真实工具调用 record 夹带返回（不影响 MemberStatement 类型，
    # 调用方负责处理）
    return stmt, tool_call_records


def _research_for_member(member_id: str, topic: str, variables: list[str]) -> MemberStatement:
    """同步版本（向后兼容测试）。"""
    raise DeprecationWarning(
        "use _research_for_member_async instead (V1-S2)"
    )


async def parallel_research(state: dict) -> dict:
    """LangGraph 节点：5 个议员**并发**调研（V1-S2 改造 + V2 ainvoke + memory）。

    V2-S3: 真用 GLM embedding + pgvector 检索历史议会摘要，注入 prompt。
    """
    # 新议会经 select_experts 后进入真正 ReAct Agent 路径。
    # 没有 selected_expert_ids 的旧测试/旧 checkpoint 继续使用 legacy 路径。
    settings = get_settings()
    if (
        state.get("selected_expert_ids")
        and settings.expert_research_mode == "react_agent"
    ):
        return await _parallel_agent_research(state)

    topic = state["topic"]
    variables = state["briefing"].get("key_variables", [])
    member_ids = list(MEMBER_TOOLS.keys())
    parliament_id = state.get("parliament_id")
    category = state.get("category")

    # V2-S3: 检索历史议会
    memories_text = "（暂无相关历史记忆）"
    try:
        from app.tools.memory_query import memory_query_async
        memories_text = await memory_query_async(
            topic_category=category or "general",
            query_text=topic,
            limit=3,
        )
    except Exception as e:
        print(f"[parallel_research] memory_query 失败: {e}")
        # 继续走默认值

    # 5 个议员并发
    tasks = [
        _research_for_member_safe(mid, topic, variables, parliament_id, memories_text)
        for mid in member_ids
    ]
    member_results = await asyncio.gather(*tasks)

    # 聚合结果
    results: dict[str, dict] = {}
    tool_calls_log: list[dict] = []
    initial_statements: list[dict] = []

    for mid, outcome in zip(member_ids, member_results):
        if outcome["ok"]:
            stmt: MemberStatement = outcome["data"]
            results[mid] = stmt.model_dump()
            # P0: 真实工具调用记录（每条是 web_search / data_table 等的真实调用）
            for tc in outcome.get("tool_calls", []):
                tool_calls_log.append({
                    "member": mid,
                    "action": "tool_call",
                    **tc,
                    "timestamp": time.time(),
                })
            # 调研完成标记
            tool_calls_log.append({
                "member": mid,
                "action": "research_complete",
                "stance": stmt.stance,
                "confidence": stmt.confidence,
                "timestamp": time.time(),
            })
            initial_statements.append({
                "speaker": mid,
                "round": 0,
                "type": "research",
                "stance": stmt.stance,
                "content": stmt.reasoning_summary,
                "evidence": [e.model_dump() for e in stmt.evidence],
                "confidence": stmt.confidence,
            })
        else:
            err = outcome["error"]
            err_str = f"{type(err).__name__}: {err}" if err else "Unknown error"
            results[mid] = {
                "member_id": mid,
                "stance": "neutral",
                "confidence": 0.0,
                "evidence": [],
                "reasoning_summary": f"[RESEARCH ERROR] {err_str}",
            }
            tool_calls_log.append({
                "member": mid,
                "action": "research_error",
                "error": err_str if isinstance(err_str, str) else str(err),
                "timestamp": time.time(),
            })
            initial_statements.append({
                "speaker": mid,
                "round": 0,
                "type": "research",
                "stance": "neutral",
                "content": f"[RESEARCH ERROR] {err_str}",
                "evidence": [],
                "confidence": 0.0,
            })

    return {
        "members": results,
        "tool_calls": tool_calls_log,
        "current_round": 0,
        "transcript": state.get("transcript", []) + initial_statements,
    }


async def _research_for_member_safe(
    member_id: str, topic: str, variables: list[str],
    parliament_id: str | None = None,
    memories: str = "（暂无相关历史记忆）",
) -> dict:
    """包装 async 调研，捕获异常返回 dict（避免单点失败拖垮 gather）。"""
    try:
        stmt, tool_call_records = await _research_for_member_async(
            member_id, topic, variables, parliament_id, memories
        )
        return {"ok": True, "data": stmt, "tool_calls": tool_call_records}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _run_one_expert_safe(
    expert_id: str,
    state: dict,
    registry: ToolRegistry,
    checkpointer=None,
) -> dict:
    """Run one professional agent without allowing a single failure to fan out."""
    settings = get_settings()
    parliament_id = state.get("parliament_id") or "_no_parliament"
    trace_sink = (
        AgentTraceSink(parliament_id)
        if state.get("parliament_id")
        else AgentTraceSink(parliament_id, writer=lambda *_: 0)
    )

    async def cancel_check() -> bool:
        if not state.get("parliament_id"):
            return False
        from app.runtime_context import is_cancelled

        return await is_cancelled(parliament_id)

    budget = AgentBudget(
        max_reason_steps=settings.agent_max_reason_steps,
        max_tool_calls=settings.agent_max_tool_calls,
        tool_timeout_seconds=settings.agent_tool_timeout_seconds,
        total_timeout_seconds=settings.agent_total_timeout_seconds,
        max_observation_chars=settings.agent_max_observation_chars,
        max_evidence=settings.agent_max_evidence,
    )
    runner = ExpertResearchAgentRunner(
        parliament_id=parliament_id,
        profile=get_expert_profile(expert_id),
        registry=registry,
        trace_sink=trace_sink,
        budget=budget,
        user_id=state.get("user_id"),
        category=state.get("category") or "general",
        sensitive_values=state.get("briefing", {}).get("sensitive_values", []),
        cancel_check=cancel_check,
    )
    user_brief = {
        "summary": state.get("briefing", {}).get("summary", state["topic"]),
        "key_variables": state.get("briefing", {}).get("key_variables", []),
        "background": state.get("user_background") or "（用户未提供额外背景）",
    }
    try:
        result_state = await asyncio.wait_for(
            runner.run(
                goal=state["topic"],
                user_brief=user_brief,
                checkpointer=checkpointer,
            ),
            timeout=settings.agent_total_timeout_seconds + 5,
        )
        return {"ok": True, "expert_id": expert_id, "state": result_state}
    except Exception as exc:
        try:
            await runner.harness.failed()
        except Exception:
            pass
        return {
            "ok": False,
            "expert_id": expert_id,
            "error": f"{type(exc).__name__}: {exc}",
            "trace_events": [
                event.model_dump(mode="json") for event in trace_sink.events
            ],
        }


def _agent_result_to_member(
    expert_id: str,
    result: ExpertResearchResult,
    evidence_records: list[dict],
) -> dict:
    evidence_by_id = {
        record.get("evidence_id"): record for record in evidence_records
    }
    evidence = []
    for ref in result.evidence_refs:
        record = evidence_by_id.get(ref)
        if not record:
            continue
        evidence.append(
            {
                "claim": record.get("claim_summary", ""),
                "source": record.get("source_url")
                or record.get("source_title")
                or "",
                "strength": record.get("strength", "medium"),
                "evidence_id": ref,
            }
        )
    return {
        "member_id": expert_id,
        "stance": result.stance,
        "confidence": result.confidence,
        "position": result.position,
        "basis_level": result.basis_level,
        "evidence": evidence,
        "reasoning_summary": result.thesis,
        "key_risks": result.key_risks,
        "assumptions": result.assumptions,
        "unknowns": result.unknowns,
        "reversal_conditions": result.reversal_conditions,
        "next_actions": result.next_actions,
        "degraded": result.degraded,
        "degradation_reason": result.degradation_reason,
    }


def _format_list(items: list[str], empty_text: str) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    return "；".join(cleaned) if cleaned else empty_text


def _render_public_research_statement(
    result: ExpertResearchResult,
    evidence_records: list[dict],
) -> str:
    """Turn the structured Agent result into one complete public statement."""
    stance_label = {
        "for": "支持",
        "against": "反对",
        "neutral": "暂不作绝对判断",
    }[result.stance]
    evidence_by_id = {
        record.get("evidence_id"): record for record in evidence_records
    }
    evidence_lines = []
    for ref in result.evidence_refs:
        record = evidence_by_id.get(ref)
        if not record:
            continue
        kind = {
            "user_fact": "用户信息",
            "web_source": "网页来源",
            "calculation": "计算结果",
            "inference": "推断假设",
        }.get(record.get("kind"), "分析依据")
        claim = str(
            record.get("supports_claim")
            or record.get("claim_summary")
            or ""
        ).strip()
        source = str(
            record.get("source_title")
            or record.get("source_domain")
            or ""
        ).strip()
        detail = claim or source
        if detail:
            evidence_lines.append(f"{kind}：{detail}")

    evidence_text = (
        "；".join(evidence_lines)
        if evidence_lines
        else "本轮没有获得可核验的外部来源，以下结论只使用用户信息与明确假设"
    )
    unknowns = list(result.unknowns)
    if result.degraded and result.degradation_reason:
        unknowns.append(result.degradation_reason)

    return "\n\n".join(
        (
            f"【我的判断】{stance_label}。{result.thesis.strip()}",
            f"【最关键的依据】{evidence_text}。",
            f"【我最担心的地方】{_format_list(result.key_risks, '关键下行风险仍需结合用户实际承受能力确认')}。",
            (
                "【当前假设与未知】"
                f"{_format_list(result.assumptions, '未使用额外假设')}；"
                f"仍需确认：{_format_list(unknowns, '暂无额外未知条件')}。"
            ),
            f"【什么时候我会改变判断】{_format_list(result.reversal_conditions, '关键条件发生实质变化时重新评估')}。",
            f"【你下一步应该做什么】{_format_list(result.next_actions, '补充最影响结论的事实后重新比较方案')}。",
        )
    )


async def _parallel_agent_research(state: dict) -> dict:
    """Run five independent professional ReAct agents in parallel."""
    selected = list(state.get("selected_expert_ids", []))[:5]
    registry = ToolRegistry(cache=ParliamentToolCache())
    checkpointer = None
    if state.get("parliament_id"):
        try:
            from app.checkpointer import get_checkpointer

            checkpointer = await get_checkpointer()
        except Exception:
            # Agent still runs; outer worker and trace store remain the fallback.
            checkpointer = None

    members: dict[str, dict] = {}
    agent_results: dict[str, dict] = {}
    trace_events: list[dict] = list(state.get("agent_traces", []))
    tool_calls = list(state.get("tool_calls", []))
    research_entries: list[dict] = []
    successes = 0

    tasks = [
        asyncio.create_task(
            _run_one_expert_safe(expert_id, state, registry, checkpointer)
        )
        for expert_id in selected
    ]
    for completed in asyncio.as_completed(tasks):
        outcome = await completed
        expert_id = outcome["expert_id"]
        if outcome["ok"]:
            successes += 1
            agent_state = outcome["state"]
            result = ExpertResearchResult.model_validate(agent_state["final_result"])
            evidence_records = agent_state.get("evidence_ledger", [])
            member = _agent_result_to_member(expert_id, result, evidence_records)
            member["reasoning_summary"] = _render_public_research_statement(
                result, evidence_records
            )
            members[expert_id] = member
            agent_results[expert_id] = {
                "result": result.model_dump(mode="json"),
                "evidence": evidence_records,
                "metrics": agent_state.get("metrics", {}),
            }
            expert_traces = agent_state.get("trace_events", [])
            trace_events.extend(expert_traces)
            for action in agent_state.get("safe_action_history", []):
                tool_calls.append({"member": expert_id, **action})
        else:
            error = outcome.get("error", "unknown agent error")
            member = {
                "member_id": expert_id,
                "stance": "neutral",
                "confidence": 0.0,
                "position": "undetermined",
                "basis_level": "limited",
                "evidence": [],
                "reasoning_summary": "该专家本轮调研未完成。",
                "key_risks": [],
                "assumptions": [],
                "unknowns": [error],
                "reversal_conditions": ["重新完成专家调研"],
                "next_actions": [],
                "degraded": True,
                "degradation_reason": error,
            }
            members[expert_id] = member
            agent_results[expert_id] = {"result": member, "evidence": [], "metrics": {}}
            trace_events.extend(outcome.get("trace_events", []))

        research_entries.append(
            {
                "speaker": expert_id,
                "round": 0,
                "type": "research",
                "stance": member["stance"],
                "content": member["reasoning_summary"],
                "evidence": member["evidence"],
                "confidence": member["confidence"],
                "position": member.get("position"),
                "basis_level": member.get("basis_level", "limited"),
                "degraded": member.get("degraded", False),
            }
        )
        # Persist and publish each result immediately. The graph node can keep
        # waiting for slower experts without hiding completed work from users.
        parliament_id = state.get("parliament_id")
        if parliament_id:
            partial_transcript = state.get("transcript", []) + research_entries
            try:
                from app.db import get_supabase
                from app.event_store import append_event

                await asyncio.to_thread(
                    lambda: (
                        get_supabase()
                        .table("parliaments")
                        .update(
                            {
                                "members_state": members,
                                "transcript": partial_transcript,
                                "current_phase": "researching",
                            }
                        )
                        .eq("id", parliament_id)
                        .execute()
                    )
                )
                await asyncio.to_thread(
                    append_event,
                    parliament_id,
                    "expert_completed",
                    {
                        "expert_id": expert_id,
                        "member": member,
                        "entry": research_entries[-1],
                        "completed_count": len(members),
                        "total_count": len(selected),
                    },
                )
            except Exception:
                # Incremental UI is best effort; the final node snapshot remains
                # the durable fallback.
                pass

    insufficient = successes < 3
    evidence_pool = list(state.get("evidence_pool", []))
    known_evidence_ids = {
        item.get("evidence_id") for item in evidence_pool if item.get("evidence_id")
    }
    for archive in agent_results.values():
        for evidence in archive.get("evidence", []):
            evidence_id = evidence.get("evidence_id")
            if evidence_id and evidence_id not in known_evidence_ids:
                evidence_pool.append(evidence)
                known_evidence_ids.add(evidence_id)
    verified_count = sum(
        evidence.get("kind") in {"user_fact", "web_source", "calculation"}
        for evidence in evidence_pool
    )
    unknown_count = len(state.get("decision_model", {}).get("unknowns", []))
    basis_level = (
        "strong"
        if verified_count >= 4 and unknown_count <= 1
        else "moderate"
        if verified_count >= 1
        else "limited"
    )
    return {
        "members": members,
        "agent_results": agent_results,
        "agent_traces": trace_events,
        "tool_calls": tool_calls,
        "current_round": 0,
        "transcript": state.get("transcript", []) + research_entries,
        "research_degraded": insufficient,
        "research_error": (
            f"仅 {successes} 位专家完成调研，当前判断基础有限"
            if insufficient
            else None
        ),
        "evidence_pool": evidence_pool,
        "basis_assessment": {
            "level": basis_level,
            "verified_count": verified_count,
            "important_unknown_count": unknown_count,
        },
    }
