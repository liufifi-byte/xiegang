import pytest
from pydantic import BaseModel

from app.agents.harness import AgentBudget, AgentBudgetExceeded, AgentHarness
from app.agents.profiles import get_expert_profile
from app.agents.schemas import ExpertResearchResult, ToolRequest, ToolResultItem
from app.agents.tool_registry import RegisteredTool, ToolContext, ToolRegistry
from app.agents.trace import AgentTraceSink


class Args(BaseModel):
    query: str


@pytest.mark.asyncio
async def test_harness_sanitizes_actual_and_display_query_and_tracks_evidence():
    captured = []

    async def handler(args: BaseModel, context: ToolContext):
        parsed = Args.model_validate(args)
        captured.append(parsed.query)
        return [
            ToolResultItem(
                title="Report",
                url="https://example.com/report",
                content="行业报告内容",
                score=0.9,
            )
        ]

    registry = ToolRegistry([RegisteredTool("web_search", Args, handler)])
    sink = AgentTraceSink("p1", writer=lambda *args: 1)
    harness = AgentHarness(
        parliament_id="p1",
        profile=get_expert_profile("economist"),
        registry=registry,
        context=ToolContext("economist", None, "offer"),
        trace_sink=sink,
        sensitive_values=["星河内部项目"],
    )
    await harness.start()
    step = await harness.before_reason()
    request = ToolRequest(
        tool_name="web_search",
        arguments={"query": "星河内部项目 13800138000 薪资"},
        public_intent="verify_public_fact",
    )
    safe_request, observation = await harness.execute_tool(request, step)
    assert observation.ok
    assert "星河内部项目" not in captured[0]
    assert "13800138000" not in captured[0]
    assert safe_request.arguments["query"] == captured[0]
    started_event = next(e for e in sink.events if e.phase == "tool_started")
    assert started_event.display_query == captured[0]
    assert len(harness.ledger.records) == 1


@pytest.mark.asyncio
async def test_harness_enforces_reason_budget():
    sink = AgentTraceSink("p1", writer=lambda *args: 1)
    harness = AgentHarness(
        parliament_id="p1",
        profile=get_expert_profile("economist"),
        registry=ToolRegistry(),
        context=ToolContext("economist", None, "general"),
        trace_sink=sink,
        budget=AgentBudget(max_reason_steps=1),
    )
    await harness.start()
    assert await harness.before_reason() == 1
    with pytest.raises(AgentBudgetExceeded):
        await harness.before_reason()


def test_harness_removes_invented_evidence_refs_and_degrades_result():
    harness = AgentHarness(
        parliament_id="p1",
        profile=get_expert_profile("economist"),
        registry=ToolRegistry(),
        context=ToolContext("economist", None, "general"),
        trace_sink=AgentTraceSink("p1", writer=lambda *args: 1),
    )
    result = ExpertResearchResult(
        agent_id="wrong",
        stance="for",
        confidence=0.9,
        thesis="结论",
        evidence_refs=["invented"],
        reversal_conditions=["条件变化"],
    )
    validated = harness.validate_result(result)
    assert validated.agent_id == "economist"
    assert validated.evidence_refs == []
    assert validated.degraded is True
    assert validated.confidence == 0.6
