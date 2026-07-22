import pytest

from app.agents.model_adapter import (
    ResilientDecisionAdapter,
    StructuredDecisionAdapter,
)
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
        self.last_tools = None
        self.last_force_finish = None

    async def decide(self, prompt, tool_schemas, *, force_finish=False):
        self.called += 1
        self.last_tools = tool_schemas
        self.last_force_finish = force_finish
        return AgentDecision(action="finish", result=result())


@pytest.mark.asyncio
async def test_resilient_adapter_falls_back_without_preselecting_action():
    fallback = FinishAdapter()
    adapter = ResilientDecisionAdapter(FailingAdapter(), fallback)
    decision = await adapter.decide("prompt", [])
    assert decision.action == "finish"
    assert fallback.called == 1


@pytest.mark.asyncio
async def test_force_finish_skips_native_and_hides_tools():
    fallback = FinishAdapter()
    adapter = ResilientDecisionAdapter(FailingAdapter(), fallback)
    decision = await adapter.decide(
        "prompt",
        [{"name": "calculator", "description": "x", "parameters": {}}],
        force_finish=True,
    )
    assert decision.action == "finish"
    assert fallback.called == 1
    assert fallback.last_tools == []
    assert fallback.last_force_finish is True


@pytest.mark.asyncio
async def test_structured_force_finish_uses_simpler_result_schema(monkeypatch):
    captured = {}

    class StructuredResult:
        async def ainvoke(self, prompt):
            return result()

    def fake_structured(schema, temperature=0.3):
        captured["schema"] = schema
        return StructuredResult()

    monkeypatch.setattr(
        "app.agents.model_adapter.get_structured_llm", fake_structured
    )
    decision = await StructuredDecisionAdapter().decide(
        "prompt", [], force_finish=True
    )
    assert captured["schema"] is ExpertResearchResult
    assert decision.action == "finish"
