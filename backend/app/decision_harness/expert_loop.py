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
from app.decision_harness.schemas import SubAgentResult

EXPERT_BASE_INSTRUCTIONS = """你是"邪杠会议室"的专家成员。你只回答被分配的专业问题，
不重新定义问题、不给整体生活建议。所有数字与事实必须可追溯；没有依据的表述
放进 unknowns，不要编造。输出遵守任务包的输出契约（只输出 SubAgentResult JSON）。
"""

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


def _parse_sub_agent_result(
    final_response: str | None, packet: ExpertTaskPacket
) -> SubAgentResult | None:
    payload = _extract_json_object(final_response or "")
    if payload is None:
        return None
    payload.setdefault("task_id", packet.task_id)
    payload.setdefault("agent_id", packet.expert_id)
    payload.setdefault("status", "completed")
    try:
        return SubAgentResult.model_validate(payload)
    except Exception:
        return None


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
                    self.agent_run_id, runtime_thread_id=thread_id
                )
            yield self._event(
                "agent_started",
                {"agent_id": packet.expert_id, "task_id": packet.task_id,
                 "thread_id": thread_id},
            )

            async with asyncio.timeout(self.timeout_seconds):
                for turn in range(self.child_turn_limit):
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
                        continue  # Turn timeout→中断→缩小重试（spec §16.3）
                    result = _parse_sub_agent_result(final_response, packet)
                    if result is not None:
                        break

                if result is None:
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
            result = self._failed_result("no_valid_sub_agent_result")

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
