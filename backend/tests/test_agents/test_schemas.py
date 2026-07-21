import pytest
from pydantic import ValidationError

from app.agents.schemas import (
    AgentDecision,
    AgentTraceEvent,
    ExpertResearchResult,
    ToolRequest,
)


def _result(**overrides):
    data = {
        "agent_id": "economist",
        "stance": "for",
        "confidence": 0.7,
        "thesis": "机会成本可控",
        "evidence_refs": [],
        "assumptions": [],
        "unknowns": [],
        "reversal_conditions": ["现金流下降"],
        "next_actions": ["核实期权条款"],
    }
    data.update(overrides)
    return ExpertResearchResult(**data)


def test_agent_decision_tool_payload_is_exclusive():
    decision = AgentDecision(
        action="tool",
        tool_request=ToolRequest(
            tool_name="web_search",
            arguments={"query": "AI salary"},
            public_intent="verify_public_fact",
        ),
    )
    assert decision.tool_request.tool_name == "web_search"
    assert decision.result is None


def test_agent_decision_finish_payload_is_exclusive():
    result = _result()
    decision = AgentDecision(action="finish", result=result)
    assert decision.result == result
    assert decision.tool_request is None


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "tool"},
        {"action": "finish"},
        {
            "action": "finish",
            "result": _result().model_dump(),
            "tool_request": {
                "tool_name": "web_search",
                "arguments": {},
                "public_intent": "verify_public_fact",
            },
        },
    ],
)
def test_agent_decision_rejects_mismatched_payload(payload):
    with pytest.raises(ValidationError):
        AgentDecision.model_validate(payload)


def test_research_result_validates_confidence_and_degradation_reason():
    with pytest.raises(ValidationError):
        _result(confidence=1.2)
    with pytest.raises(ValidationError):
        _result(degraded=True)
    degraded = _result(degraded=True, degradation_reason="tool budget exhausted")
    assert degraded.degraded is True


def test_research_result_requires_reversal_condition():
    with pytest.raises(ValidationError):
        _result(reversal_conditions=[])


def test_trace_event_is_json_serializable():
    event = AgentTraceEvent(
        trace_id="trace-1",
        run_id="run-1",
        agent_id="economist",
        step=1,
        phase="deciding",
        public_message="正在判断信息缺口",
        timestamp=1.0,
    )
    assert '"agent_id":"economist"' in event.model_dump_json()
