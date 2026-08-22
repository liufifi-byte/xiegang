"""子 Agent Loop（spec 2026-08-22 §8，Phase 2：经济学家垂直切片）。

单一子 Agent 的完整生命周期：建线程 → 逐轮执行 → 结构化 Write-back →
partial/failed 退出 → 线程保留可恢复（follow-up 用）。

失败隔离铁律（Phase 2 验收）：本模块对上层只产事件与 SubAgentResult，
任何异常（含超时、协议错误、Runtime 崩溃）都收敛为 status="failed" 的
结果行，绝不把异常抛回 Root Workflow（spec §8.4）。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

from app.agents.profiles import EXPERT_PROFILES
from app.decision_harness.context_compiler import (
    ContextPacket,
    ExpertTaskPacket,
    render_child_finalize_prompt,
    render_child_prompt,
)
from app.decision_harness.event_bridge import (
    make_product_event,
    translate_runtime_event,
)
from app.decision_harness.repository import DecisionRunRepository
from app.decision_harness.runtime import AgentRuntime
from app.decision_harness.schemas import SubAgentResult, normalize_legacy_stance

EXPERT_BASE_INSTRUCTIONS = """你是"邪杠会议室"的专家成员。你只回答被分配的专业问题，
不重新定义问题、不给整体生活建议。所有数字与事实必须可追溯；没有依据的表述
放进 unknowns，不要编造。输出遵守任务包的输出契约（只输出 SubAgentResult JSON）。

立场要求（会议立场需要多元化，你不需要与其他专家一致）：
- 若某方案在明确条件下值得做，直接给 "support" 并写清条件——不要防御性地一律 cautious；
- 给 "cautious" 时必须写明"满足什么条件即转为支持"；
- 判断简洁：judgment ≤120 字，analysis_points 每条 ≤80 字，总输出 ≤600 字。
"""

# spec §4.4：相关性低于阈值的专家不发言（转 silent，不生成伪观点）
SILENCE_RELEVANCE_THRESHOLD = 0.3

_JUDGMENT_MAX_CHARS = 500


@dataclass
class ExpertRunOutcome:
    agent_run_id: str
    session_id: str
    packet: ExpertTaskPacket
    result: SubAgentResult
    turns_used: int
    thread_id: str | None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text or not text.strip():
        return None
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        if body.startswith("json"):
            body = body[4:]
        body = body.strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _coerce_sub_agent_payload(payload: dict, packet: ExpertTaskPacket) -> dict:
    """真跑实测（彩排）：模型常超列表上限或用非法枚举——按契约裁剪/归一，
    而不是整份拒收（contrarian 因 4 条 reversal_conditions 全军覆没的教训）。
    """
    caps = {
        "analysis_points": 3, "evidence_refs": 5, "assumptions": 5,
        "unknowns": 5, "reversal_conditions": 3,
        "questions_for_other_agents": 3, "questions_for_user": 2,
    }
    for field, cap in caps.items():
        value = payload.get(field)
        if isinstance(value, list):
            payload[field] = [str(v)[:2000] for v in value[:cap]]
        elif value is None:
            payload[field] = []
    for field, limit in (("judgment", 500), ("action", 300)):
        if isinstance(payload.get(field), str):
            payload[field] = payload[field][:limit]
    if payload.get("stance") not in ("support", "oppose", "cautious", "neutral"):
        payload["stance"] = normalize_legacy_stance(str(payload.get("stance") or "neutral"))
        if payload["stance"] not in ("support", "oppose", "cautious", "neutral"):
            payload["stance"] = "neutral"
    if payload.get("status") not in ("completed", "partial", "silent", "failed"):
        payload["status"] = "completed"
    # silent 带观点：内容在场则视为 completed（不生成伪观点的反向同样成立）
    if payload.get("status") == "silent" and (
        str(payload.get("judgment") or "").strip() or payload.get("analysis_points")
    ):
        payload["status"] = "completed"
    try:
        payload["relevance"] = max(0.0, min(1.0, float(payload.get("relevance", 0.7))))
        payload["confidence"] = max(0.0, min(1.0, float(payload.get("confidence", 0.5))))
    except (TypeError, ValueError):
        pass
    return payload


def _parse_sub_agent_result(
    final_response: str | None, packet: ExpertTaskPacket
) -> SubAgentResult | None:
    payload = _extract_json_object(final_response or "")
    if payload is None:
        return None
    payload.setdefault("task_id", packet.task_id)
    payload.setdefault("agent_id", packet.expert_id)
    payload.setdefault("status", "completed")
    payload = _coerce_sub_agent_payload(payload, packet)
    try:
        return SubAgentResult.model_validate(payload)
    except Exception:
        return None


def enforce_relevance_gate(result: SubAgentResult) -> SubAgentResult:
    """spec §4.4：低相关专家不发言——completed 且 relevance 低于阈值 → silent。

    silent 契约：不带观点（judgment 清空、analysis_points 清空），
    至多保留 1 个用户问题。自报 silent/failed/partial 的结果不动。
    """
    if result.status != "completed":
        return result
    if result.relevance >= SILENCE_RELEVANCE_THRESHOLD:
        return result
    return result.model_copy(update={
        "status": "silent",
        "judgment": "",
        "analysis_points": [],
        "questions_for_user": result.questions_for_user[:1],
        "confidence": min(result.confidence, 0.3),
        "relevance": result.relevance,  # 保留原始相关度供审计
    })


def check_role_boundary(result: SubAgentResult) -> str | None:
    """结构性角色越界检查（语义级检查属 Phase 4 Evidence Kernel）。

    返回越界说明；None = 通过。通过时置 completion.boundary_checked。
    """
    if "http" in (result.judgment or "") and not result.evidence_refs:
        return "judgment_cites_url_without_evidence_refs"
    return None


def _with_boundary_checked(result: SubAgentResult) -> SubAgentResult:
    completion = result.completion.model_copy(update={"boundary_checked": True})
    return result.model_copy(update={"completion": completion})


class ExpertAgentRunner:
    """跑一个专家任务。用法与 DecisionWorkflow 一致：

        runner = ExpertAgentRunner(run_id=..., packet=..., context=...,
                                   runtime=..., repo=...)
        async for event in runner.run(): ...
        outcome = runner.outcome  # run() 耗尽后有效
    """

    def __init__(
        self,
        *,
        run_id: str,
        packet: ExpertTaskPacket,
        context: ContextPacket,
        runtime: AgentRuntime,
        repo: DecisionRunRepository,
        child_turn_limit: int = 4,
        timeout_seconds: float = 300.0,
    ):
        self.run_id = run_id
        self.packet = packet
        self.context = context
        self.runtime = runtime
        self.repo = repo
        self.child_turn_limit = max(1, child_turn_limit)
        self.timeout_seconds = timeout_seconds
        self.outcome: ExpertRunOutcome | None = None
        self.agent_run_id = f"{run_id}:agent:{packet.task_id}"
        self.session_id = f"{run_id}:expert:{packet.task_id}"
        self._cancel_requested = False

    def request_cancel(self) -> None:
        """调度器/用户取消：当前轮可被 interrupt 打断，后续轮不再开始。"""
        self._cancel_requested = True

    def _event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return make_product_event(self.run_id, event_type, payload)

    def _failed_result(self, reason: str) -> SubAgentResult:
        return SubAgentResult(
            task_id=self.packet.task_id,
            agent_id=self.packet.expert_id,
            status="failed",
            judgment="",
            unknowns=[reason[:200]],
            confidence=0.0,
        )

    async def run(self) -> AsyncIterator[dict[str, Any]]:
        packet, repo, runtime = self.packet, self.repo, self.runtime
        run_id = self.run_id
        session_id = self.session_id

        # 登记行先行：即使 Runtime 当场崩溃，failed 结果也有行可写
        await repo.upsert_agent_run(
            self.agent_run_id,
            decision_run_id=run_id,
            agent_type="expert",
            expert_id=packet.expert_id,
            runtime_provider=type(runtime).__name__,
            task_id=packet.task_id,
            status="running",
            task_packet=packet.model_dump(mode="json"),
            started=True,
        )

        result: SubAgentResult | None = None
        turns_used = 0
        last_agent_text: str | None = None
        thread_id: str | None = None

        try:
            profile = get_expert_profile(packet.expert_id)
            await runtime.start_session(
                session_id, instructions=EXPERT_BASE_INSTRUCTIONS + profile.role_prompt
            )
            thread_id_getter = getattr(runtime, "runtime_thread_id", None)
            thread_id = (
                thread_id_getter(session_id) if callable(thread_id_getter) else None
            )
            if thread_id:
                await repo.upsert_agent_run(
                    self.agent_run_id, decision_run_id=run_id,
                    agent_type="expert", runtime_thread_id=thread_id,
                )
            yield self._event(
                "agent_started",
                {"agent_id": packet.expert_id, "task_id": packet.task_id,
                 "thread_id": thread_id},
            )

            async with asyncio.timeout(self.timeout_seconds):
                for turn in range(self.child_turn_limit):
                    if self._cancel_requested:
                        break  # 取消后不再开新轮（spec §8.4 任务取消）
                    prompt = (
                        render_child_prompt(packet, self.context)
                        if turn == 0
                        else (
                            "继续你的分析。若信息已足够，立即只输出最终 "
                            "SubAgentResult JSON；若还想使用工具，先说明要核实什么。"
                        )
                    )
                    final_response: str | None = None
                    interrupted = False
                    async for event in runtime.start_turn(session_id, prompt):
                        translated = translate_runtime_event(run_id, event)
                        if translated is not None:
                            # 子 Agent 事件把归属从 main 改成专家 id
                            translated["payload"]["agent"] = packet.expert_id
                            translated["payload"]["task_id"] = packet.task_id
                            yield translated
                        if event.type == "turn_final":
                            final_response = event.payload.get("final_response")
                        elif event.type == "turn_interrupted":
                            interrupted = True
                        elif event.type == "item_completed":
                            item = event.payload.get("item")
                            if isinstance(item, dict) and item.get("text"):
                                last_agent_text = item["text"]
                    turns_used = turn + 1
                    if interrupted:
                        if self._cancel_requested:
                            break
                        continue  # Turn timeout→中断→缩小重试（spec §16.3）
                    result = _parse_sub_agent_result(final_response, packet)
                    if result is not None:
                        break

                if result is None and not self._cancel_requested:
                    # 预算将尽：强制收敛一轮（partial 退出路径）
                    final_response = None
                    async for event in runtime.start_turn(
                        session_id, render_child_finalize_prompt(packet)
                    ):
                        translated = translate_runtime_event(run_id, event)
                        if translated is not None:
                            translated["payload"]["agent"] = packet.expert_id
                            translated["payload"]["task_id"] = packet.task_id
                            yield translated
                        if event.type == "turn_final":
                            final_response = event.payload.get("final_response")
                    turns_used += 1
                    result = _parse_sub_agent_result(final_response, packet)
        except TimeoutError:
            # 超时 → partial：只携带真实见过、且足以构成"安全有限结论"的内容
            if last_agent_text:
                result = SubAgentResult(
                    task_id=packet.task_id,
                    agent_id=packet.expert_id,
                    status="partial",
                    judgment=last_agent_text[:_JUDGMENT_MAX_CHARS],
                    unknowns=["响应超时，分析未完成"],
                    confidence=0.3,
                )
            else:
                result = self._failed_result("expert_timeout")
        except Exception as exc:  # 失败隔离：任何异常都不穿透到 Root
            result = self._failed_result(f"expert_error:{type(exc).__name__}")

        if result is None:
            if self._cancel_requested:
                result = self._failed_result("expert_cancelled")
            else:
                result = self._failed_result("no_valid_sub_agent_result")

        # 角色越界检查在相关度门之前：检查的是子 Agent 的原始输出
        boundary_note = check_role_boundary(result)
        # 相关性门（spec §4.4 低相关不发言）
        result = enforce_relevance_gate(result)
        result = _with_boundary_checked(result)
        if boundary_note:
            yield self._event(
                "run_log",
                {"note": "role_boundary", "task_id": packet.task_id,
                 "detail": boundary_note},
            )

        # 结构化 Write-back（spec §11.3：子 Agent 只写回 Delta，不改 Canonical State）
        await repo.upsert_agent_run(
            self.agent_run_id,
            decision_run_id=run_id,
            agent_type="expert",
            expert_id=packet.expert_id,
            status=result.status,
            result=result.model_dump(mode="json"),
            error_code=None if result.status != "failed" else "expert_failed",
            finished=True,
        )
        if result.status == "failed":
            yield self._event(
                "agent_failed",
                {"agent_id": packet.expert_id, "task_id": packet.task_id,
                 "message": (result.unknowns[0] if result.unknowns else "failed")},
            )
        else:
            yield self._event(
                "agent_completed",
                {"agent_id": packet.expert_id, "task_id": packet.task_id,
                 "status": result.status, "stance": result.stance,
                 "confidence": result.confidence},
            )
        # 线程保留在 Runtime 侧，可 thread_resume 做 follow-up（spec §10.2）
        await runtime.close_session(session_id)
        self.outcome = ExpertRunOutcome(
            agent_run_id=self.agent_run_id,
            session_id=session_id,
            packet=packet,
            result=result,
            turns_used=turns_used,
            thread_id=thread_id,
        )


def get_expert_profile(expert_id: str):
    """从八人库取 Profile；Phase 2 垂直切片只期望 economist 在列。"""
    profile = EXPERT_PROFILES.get(expert_id)
    if profile is None:
        raise KeyError(f"未知专家 ID: {expert_id!r}")
    return profile


async def send_followup(
    *,
    run_id: str,
    agent_run_row: dict[str, Any],
    questions: list[str],
    runtime: AgentRuntime,
    repo: DecisionRunRepository,
    timeout_seconds: float = 120.0,
) -> tuple[SubAgentResult, bool, list[dict[str, Any]]]:
    """对已完成的子 Agent 追问（spec §10.2 follow-up）。

    恢复该专家的 Runtime 线程（agent_runs.runtime_thread_id），发一轮
    follow-up，返回 (新结果, 是否成功, 产品事件列表)。失败时返回旧结果，
    不抛异常（与 ExpertAgentRunner 相同的隔离铁律）。
    """
    events: list[dict[str, Any]] = []
    task_id = agent_run_row.get("task_id") or ""
    expert_id = agent_run_row.get("expert_id") or ""
    old_result = SubAgentResult.model_validate(agent_run_row["result"])
    thread_id = agent_run_row.get("runtime_thread_id")
    if not thread_id:
        events.append(make_product_event(run_id, "run_log", {
            "note": "followup_no_thread", "task_id": task_id}))
        return old_result, False, events

    session_id = f"{run_id}:expert:{task_id}"
    try:
        await runtime.resume_session(session_id, runtime_thread_id=thread_id)
        prompt = (
            "主 Agent 对你的结论有追问，请只针对问题回答，输出更新后的完整 "
            "SubAgentResult JSON：\n"
            + "\n".join(f"- {q}" for q in questions)
        )
        final_response: str | None = None
        async with asyncio.timeout(timeout_seconds):
            async for event in runtime.start_turn(session_id, prompt):
                translated = translate_runtime_event(run_id, event)
                if translated is not None:
                    translated["payload"]["agent"] = expert_id
                    translated["payload"]["task_id"] = task_id
                    events.append(translated)
                if event.type == "turn_final":
                    final_response = event.payload.get("final_response")
        packet = ExpertTaskPacket.model_validate(agent_run_row["task_packet"])
        new_result = _parse_sub_agent_result(final_response, packet)
        if new_result is None:
            events.append(make_product_event(run_id, "run_log", {
                "note": "followup_invalid_response", "task_id": task_id}))
            return old_result, False, events
        new_result = enforce_relevance_gate(new_result)
        new_result = _with_boundary_checked(new_result)
        await repo.upsert_agent_run(
            agent_run_row["id"],
            decision_run_id=run_id,
            agent_type="expert",
            expert_id=expert_id,
            status=new_result.status,
            result=new_result.model_dump(mode="json"),
            finished=True,
        )
        events.append(make_product_event(run_id, "agent_completed", {
            "agent_id": expert_id, "task_id": task_id,
            "status": new_result.status, "followup": True,
        }))
        await runtime.close_session(session_id)
        return new_result, True, events
    except Exception as exc:
        events.append(make_product_event(run_id, "run_log", {
            "note": "followup_error", "task_id": task_id,
            "detail": f"{type(exc).__name__}"[:120]}))
        return old_result, False, events
