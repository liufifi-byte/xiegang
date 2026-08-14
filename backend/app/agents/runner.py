"""Convenient execution boundary for one expert ReAct graph."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.agents.harness import (
    AgentBudget,
    AgentBudgetExceeded,
    AgentCancelled,
    AgentHarness,
)
from app.agents.model_adapter import DecisionAdapter, ResilientDecisionAdapter
from app.agents.profiles import ExpertProfile
from app.agents.research_graph import (
    ExpertResearchGraph,
    _evidence_backed_fallback,
    _fallback_result,
)
from app.agents.tool_registry import ToolContext, ToolRegistry
from app.agents.trace import AgentTraceSink

logger = logging.getLogger(__name__)


class ExpertResearchAgentRunner:
    def __init__(
        self,
        *,
        parliament_id: str,
        profile: ExpertProfile,
        registry: ToolRegistry,
        trace_sink: AgentTraceSink,
        adapter: DecisionAdapter | None = None,
        budget: AgentBudget | None = None,
        user_id: str | None = None,
        category: str = "general",
        sensitive_values: list[str] | None = None,
        cancel_check=None,
        duty_hint: str | None = None,
        force_stance: str | None = None,
    ):
        self.profile = profile
        self.harness = AgentHarness(
            parliament_id=parliament_id,
            profile=profile,
            registry=registry,
            context=ToolContext(profile.expert_id, user_id, category),
            trace_sink=trace_sink,
            budget=budget,
            sensitive_values=sensitive_values,
            cancel_check=cancel_check,
        )
        self.adapter = adapter or ResilientDecisionAdapter()
        # duties 字段已分配但未传 LLM 的修复：把 duty_hint + force_stance 透传给图
        self.duty_hint = duty_hint
        self.force_stance = force_stance

    async def run(
        self,
        *,
        goal: str,
        user_brief: dict[str, Any],
        checkpointer=None,
    ) -> dict[str, Any]:
        await self.harness.start()
        graph = ExpertResearchGraph(
            self.profile, self.harness, self.adapter,
            duty_hint=self.duty_hint,
            force_stance=self.force_stance,
        ).compile(checkpointer=checkpointer)
        state = {
            "run_id": self.harness.run_id,
            "parliament_id": self.harness.parliament_id,
            "agent_id": self.profile.expert_id,
            "expert_profile_id": self.profile.expert_id,
            "goal": goal,
            "user_brief": user_brief,
            "category": self.harness.context.category,
            "messages": [],
            "observations": [],
            "evidence_ledger": [],
            "safe_action_history": [],
            "reason_step_count": 0,
            "tool_call_count": 0,
            "started_at": time.time(),
            "status": "running",
            "last_error": None,
            "decision": None,
            "pending_observation": None,
            "final_result": None,
            "metrics": {},
        }
        config = {"configurable": {"thread_id": self.harness.run_id}}
        try:
            final_state = await asyncio.wait_for(
                graph.ainvoke(state, config=config),
                timeout=max(0.01, self.harness.budget.remaining_seconds),
            )
        except (TimeoutError, AgentBudgetExceeded) as budget_exc:
            # P0 修订：reason 文案不要写"总运行时间已达上限"——这是误导用户。
            # 真实异常（TimeoutError vs AgentBudgetExceeded）+ 真实信息只写入 degradation_reason
            # 字段供日志；用户看不到内部错误码。
            reason_kind = (
                "时间上限"
                if isinstance(budget_exc, TimeoutError)
                else "步骤预算"
            )
            logger.warning(
                "Agent %s 触发 %s 限制: %s",
                self.profile.expert_id, reason_kind, budget_exc,
            )
            reason = f"受本次调研{reason_kind}限制，未能完成完整推理"
            result = self.harness.validate_result(
                _evidence_backed_fallback(self.profile, self.harness, reason),
                forced_reason=reason,
            )
            if (
                not self.harness.trace_sink.events
                or self.harness.trace_sink.events[-1].phase != "finalizing"
            ):
                await self.harness.before_finalize(
                    self.harness.budget.reason_steps_used
                )
            await self.harness.complete(result)
            final_state = {
                **state,
                "status": "degraded",
                "last_error": "agent_total_timeout",
                "final_result": result.model_dump(mode="json"),
                "evidence_ledger": [
                    record.model_dump(mode="json")
                    for record in self.harness.ledger.records
                ],
                "safe_action_history": [
                    action.model_dump(mode="json")
                    for action in self.harness.safe_actions
                ],
            }
        except AgentCancelled:
            await self.harness.failed()
            result = _fallback_result(self.profile.expert_id, "用户取消了会议")
            return {
                **state,
                "status": "cancelled",
                "final_result": result.model_dump(mode="json"),
            }
        final_state["metrics"] = {
            "reason_steps": self.harness.budget.reason_steps_used,
            "tool_calls": self.harness.budget.tool_calls_used,
            "duration_ms": int(self.harness.budget.elapsed_seconds * 1000),
        }
        final_state["trace_events"] = [
            event.model_dump(mode="json") for event in self.harness.trace_sink.events
        ]
        return final_state
