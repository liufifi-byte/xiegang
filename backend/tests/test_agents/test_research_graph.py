import pytest
from pydantic import BaseModel

from app.agents.harness import AgentBudget
from app.agents.profiles import get_expert_profile
from app.agents.runner import ExpertResearchAgentRunner
from app.agents.schemas import AgentDecision, ExpertResearchResult, ToolRequest, ToolResultItem
from app.agents.tool_registry import RegisteredTool, ToolContext, ToolRegistry
from app.agents.trace import AgentTraceSink


def finish_result(evidence_refs=None):
    return ExpertResearchResult(
        agent_id="economist",
        stance="cautious",
        confidence=0.75,
        judgment="机会成本可控，谨慎推进。",
        evidence_refs=evidence_refs or [],
        reversal_condition="现金流恶化。",
        action="核实条款。",
    )


class SequenceAdapter:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    async def decide(self, prompt, tool_schemas, *, force_finish=False):
        if force_finish:
            return AgentDecision(action="finish", result=finish_result())
        return self.decisions.pop(0)


class SearchArgs(BaseModel):
    query: str


async def search_handler(args: BaseModel, context: ToolContext):
    return [
        ToolResultItem(
            title="Market report",
            url="https://example.com/market",
            content="市场薪资保持稳定",
            score=0.9,
        )
    ]


def runner(adapter, budget=None):
    return ExpertResearchAgentRunner(
        parliament_id="p1",
        profile=get_expert_profile("economist"),
        registry=ToolRegistry([RegisteredTool("web_search", SearchArgs, search_handler)]),
        trace_sink=AgentTraceSink("p1", writer=lambda *args: 1),
        adapter=adapter,
        budget=budget,
        category="offer",
    )


@pytest.mark.asyncio
async def test_graph_can_finish_without_tools():
    state = await runner(
        SequenceAdapter([AgentDecision(action="finish", result=finish_result())])
    ).run(goal="是否接 Offer", user_brief={"background": "信息充分"})
    assert state["final_result"]["stance"] == "cautious"
    assert state["metrics"]["tool_calls"] == 0
    phases = [event["phase"] for event in state["trace_events"]]
    assert phases == ["started", "deciding", "finalizing", "completed"]


@pytest.mark.asyncio
async def test_graph_reacts_to_tool_observation_then_finishes():
    search = AgentDecision(
        action="tool",
        tool_request=ToolRequest(
            tool_name="web_search",
            arguments={"query": "AI 产品经理薪资"},
            public_intent="verify_public_fact",
        ),
    )
    state = await runner(SequenceAdapter([search, AgentDecision(action="finish", result=finish_result())])).run(
        goal="是否接 Offer", user_brief={}
    )
    assert state["metrics"]["tool_calls"] == 1
    assert len(state["evidence_ledger"]) == 1
    assert len(state["observations"]) == 1


@pytest.mark.asyncio
async def test_graph_forces_finalization_when_reason_budget_is_exhausted():
    search = AgentDecision(
        action="tool",
        tool_request=ToolRequest(
            tool_name="web_search",
            arguments={"query": "AI 产品经理薪资"},
            public_intent="verify_public_fact",
        ),
    )
    state = await runner(
        SequenceAdapter([search]),
        budget=AgentBudget(max_reason_steps=1, max_tool_calls=2),
    ).run(goal="是否接 Offer", user_brief={})
    assert state["final_result"]["degraded"] is True
    assert state["status"] == "degraded"
