import pytest

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
                thesis="需要更多信息",
                reversal_conditions=["补充现金流信息"],
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
