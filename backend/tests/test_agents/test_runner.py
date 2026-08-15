import asyncio

import pytest

from app.agents.harness import AgentBudget
from app.agents.profiles import get_expert_profile
from app.agents.runner import ExpertResearchAgentRunner
from app.agents.schemas import AgentDecision, ExpertResearchResult
from app.agents.tool_registry import ToolRegistry
from app.agents.trace import AgentTraceSink


class FinishAdapter:
    async def decide(self, prompt, tool_schemas, *, force_finish=False):
        return AgentDecision(
            action="finish",
            result=ExpertResearchResult(
                agent_id="economist",
                stance="neutral",
                confidence=0.5,
                judgment="需要更多信息，暂缓判断。",
                reversal_condition="补充现金流信息后重评。",
            ),
        )


@pytest.mark.asyncio
async def test_runner_returns_metrics_and_safe_trace():
    runner = ExpertResearchAgentRunner(
        parliament_id="p1",
        profile=get_expert_profile("economist"),
        registry=ToolRegistry(),
        trace_sink=AgentTraceSink("p1", writer=lambda *args: 1),
        adapter=FinishAdapter(),
    )
    state = await runner.run(goal="是否接 Offer", user_brief={})
    assert state["metrics"]["reason_steps"] == 1
    assert state["metrics"]["tool_calls"] == 0
    assert state["trace_events"]
    serialized = str(state["trace_events"])
    assert "system prompt" not in serialized.lower()


class HangingAdapter:
    async def decide(self, prompt, tool_schemas, *, force_finish=False):
        await asyncio.sleep(1)
        raise AssertionError("the runner should cancel a hung model call")


@pytest.mark.asyncio
async def test_runner_enforces_total_timeout_around_model_calls():
    runner = ExpertResearchAgentRunner(
        parliament_id="p-timeout",
        profile=get_expert_profile("economist"),
        registry=ToolRegistry(),
        trace_sink=AgentTraceSink("p-timeout", writer=lambda *args: 1),
        adapter=HangingAdapter(),
        budget=AgentBudget(total_timeout_seconds=0.01),
    )
    state = await runner.run(goal="是否接 Offer", user_brief={})
    assert state["status"] == "degraded"
    assert state["last_error"] == "agent_total_timeout"
    assert state["final_result"]["degraded"] is True
    assert state["metrics"]["duration_ms"] < 500
    assert [event["phase"] for event in state["trace_events"]][-2:] == [
        "finalizing",
        "degraded",
    ]
