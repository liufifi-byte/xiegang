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

    query_str = f"{topic} {' '.join(variables[:3])}"
    for tool in tools[:MAX_TOOL_CALLS_PER_MEMBER]:
        try:
            results = tool(query_str)
            for r in results[:2]:
                tool_results.append(
                    f"- [{r.get('title', '')}]({r.get('url', '')}) "
                    f"{r.get('content', '')[:200]}"
                )
        except Exception as e:
            tool_results.append(f"- [TOOL ERROR] {e}")

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
        return await guard_ainvoke(
            parliament_id=parliament_id or "_no_parliament",
            coro=structured.ainvoke(prompt),
            timeout=60,  # P0-S1.2: 单 LLM 调用硬上限 60s
        )


def _research_for_member(member_id: str, topic: str, variables: list[str]) -> MemberStatement:
    """同步版本（向后兼容测试）。"""
    raise DeprecationWarning(
        "use _research_for_member_async instead (V1-S2)"
    )


async def parallel_research(state: dict) -> dict:
    """LangGraph 节点：5 个议员**并发**调研（V1-S2 改造 + V2 ainvoke + memory）。

    V2-S3: 真用 GLM embedding + pgvector 检索历史议会摘要，注入 prompt。
    """
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
            results[mid] = {
                "member_id": mid,
                "stance": "neutral",
                "confidence": 0.0,
                "evidence": [],
                "reasoning_summary": f"[RESEARCH ERROR] {err}",
            }
            tool_calls_log.append({
                "member": mid,
                "action": "research_error",
                "error": str(err),
                "timestamp": time.time(),
            })
            initial_statements.append({
                "speaker": mid,
                "round": 0,
                "type": "research",
                "stance": "neutral",
                "content": f"[RESEARCH ERROR] {err}",
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
        stmt = await _research_for_member_async(
            member_id, topic, variables, parliament_id, memories
        )
        return {"ok": True, "data": stmt}
    except Exception as e:
        return {"ok": False, "error": e}
