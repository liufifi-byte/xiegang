import re

import pytest

from app.agents.harness import AgentBudget
from app.agents.profiles import get_expert_profile
from app.agents.runner import ExpertResearchAgentRunner
from app.agents.schemas import (
    AgentDecision,
    ExpertResearchResult,
    ToolRequest,
    ToolResultItem,
)
from app.agents.tool_registry import (
    CalculatorArgs,
    RegisteredTool,
    ToolContext,
    ToolRegistry,
    WebSearchArgs,
)
from app.agents.trace import AgentTraceSink
from app.tools.calculator import safe_calculate


class EconomistSequenceAdapter:
    def __init__(self):
        self.calls = 0

    async def decide(self, prompt, tool_schemas, *, force_finish=False):
        self.calls += 1
        if self.calls == 1:
            return AgentDecision(
                action="tool",
                tool_request=ToolRequest(
                    tool_name="web_search",
                    arguments={
                        "query": "星河内部项目 刘小明 13800138000 产品负责人 市场薪资"
                    },
                    public_intent="verify_public_fact",
                ),
            )
        if self.calls == 2:
            return AgentDecision(
                action="tool",
                tool_request=ToolRequest(
                    tool_name="calculator",
                    arguments={"expression": "300000 * 1.2 - 300000"},
                    public_intent="quantify_tradeoff",
                ),
            )
        evidence_match = re.search(r"\[(ev_[a-f0-9]{12})\]", prompt)
        assert evidence_match is not None
        return AgentDecision(
            action="finish",
            result=ExpertResearchResult(
                agent_id="economist",
                stance="for",
                confidence=0.72,
                thesis="现金增量明确，但股权和职责边界仍决定真实机会成本。",
                evidence_refs=[evidence_match.group(1)],
                assumptions=["新岗位现金薪资确实提高20%"],
                unknowns=["股权兑现概率", "职责边界"],
                reversal_conditions=["现金流安全垫不足六个月", "核心条款无法写入合同"],
                next_actions=["核实股权条款", "与招聘方确认决策权"],
            ),
        )


@pytest.mark.asyncio
async def test_economist_vertical_slice_reacts_safely_and_cites_real_evidence():
    captured_queries: list[str] = []

    async def search_handler(args, context: ToolContext):
        parsed = WebSearchArgs.model_validate(args)
        captured_queries.append(parsed.query)
        return [
            ToolResultItem(
                title="产品负责人薪酬报告",
                url="https://example.com/salary-report",
                content="同阶段公司岗位总现金薪酬区间保持稳定。",
                score=0.9,
            )
        ]

    async def calculator_handler(args, context: ToolContext):
        parsed = CalculatorArgs.model_validate(args)
        value = safe_calculate(parsed.expression, parsed.variables)
        return [
            ToolResultItem(
                title="安全计算结果",
                content=f"年度现金增量为 {value}",
                score=1.0,
            )
        ]

    registry = ToolRegistry(
        [
            RegisteredTool("web_search", WebSearchArgs, search_handler),
            RegisteredTool("calculator", CalculatorArgs, calculator_handler),
        ]
    )
    runner = ExpertResearchAgentRunner(
        parliament_id="p-economist",
        profile=get_expert_profile("economist"),
        registry=registry,
        trace_sink=AgentTraceSink("p-economist", writer=lambda *args: 1),
        adapter=EconomistSequenceAdapter(),
        budget=AgentBudget(max_reason_steps=3, max_tool_calls=2),
        category="offer",
        sensitive_values=["星河内部项目", "刘小明"],
    )

    state = await runner.run(
        goal="是否接受一家成长期公司的产品负责人 Offer",
        user_brief={"cash_salary": 300000, "raise": "20%", "runway_months": 9},
    )

    assert state["status"] == "completed"
    assert state["metrics"]["reason_steps"] == 3
    assert state["metrics"]["tool_calls"] == 2
    assert state["final_result"]["degraded"] is False
    assert len(state["final_result"]["evidence_refs"]) == 1
    assert state["final_result"]["unknowns"]
    assert state["final_result"]["reversal_conditions"]

    assert len(captured_queries) == 1
    assert "星河内部项目" not in captured_queries[0]
    assert "刘小明" not in captured_queries[0]
    assert "13800138000" not in captured_queries[0]

    serialized_trace = str(state["trace_events"])
    assert "星河内部项目" not in serialized_trace
    assert "刘小明" not in serialized_trace
    assert "13800138000" not in serialized_trace
    assert [event["phase"] for event in state["trace_events"]] == [
        "started",
        "deciding",
        "tool_started",
        "tool_completed",
        "evidence_added",
        "deciding",
        "tool_started",
        "tool_completed",
        "evidence_added",
        "deciding",
        "finalizing",
        "completed",
    ]
