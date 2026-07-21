import pytest

from app.agents.model_adapter import ResilientDecisionAdapter
from app.agents.schemas import AgentDecision, ExpertResearchResult


def result():
    return ExpertResearchResult(
        agent_id="economist",
        stance="neutral",
        confidence=0.5,
        thesis="信息有限",
        reversal_conditions=["补充信息"],
    )


class FailingAdapter:
    async def decide(self, prompt, tool_schemas, *, force_finish=False):
        raise ValueError("native tool call failed")


class FinishAdapter:
    def __init__(self):
        self.called = 0

    async def decide(self, prompt, tool_schemas, *, force_finish=False):
        self.called += 1
        return AgentDecision(action="finish", result=result())


@pytest.mark.asyncio
async def test_resilient_adapter_falls_back_without_preselecting_action():
    fallback = FinishAdapter()
    adapter = ResilientDecisionAdapter(FailingAdapter(), fallback)
    decision = await adapter.decide("prompt", [])
    assert decision.action == "finish"
    assert fallback.called == 1
