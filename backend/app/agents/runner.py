"""Convenient execution boundary for one expert ReAct graph."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.agents.harness import AgentBudget, AgentCancelled, AgentHarness
from app.agents.model_adapter import DecisionAdapter, ResilientDecisionAdapter
from app.agents.profiles import ExpertProfile
from app.agents.research_graph import ExpertResearchGraph, _fallback_result
from app.agents.tool_registry import ToolContext, ToolRegistry
from app.agents.trace import AgentTraceSink


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

    async def run(
        self,
        *,
        goal: str,
        user_brief: dict[str, Any],
        checkpointer=None,
    ) -> dict[str, Any]:
        await self.harness.start()
        graph = ExpertResearchGraph(
            self.profile, self.harness, self.adapter
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
        except TimeoutError:
            reason = "Agent 总运行时间已达到上限"
            result = self.harness.validate_result(
                _fallback_result(self.profile.expert_id, reason),
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
