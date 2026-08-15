import pytest
from pydantic import ValidationError

from app.agents.schemas import (
    AgentDecision,
    AgentTraceEvent,
    EvidenceRecord,
    ExpertResearchResult,
    ToolRequest,
)


def _result(**overrides):
    data = {
        "agent_id": "economist",
        "stance": "cautious",
        "confidence": 0.7,
        "judgment": "机会成本可控，建议谨慎推进",
        "lens_title": "成本与回报",
        "analysis_points": [],
        "action": "",
        "key_question": "",
        "reversal_condition": "",
        "evidence_refs": [],
        "assumptions": [],
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


def test_research_result_allows_empty_optional_fields():
    """V3：action/key_question/reversal_condition 全部可空（spec：不强行凑满）。"""
    result = _result(action="", key_question="", reversal_condition="")
    assert result.action == ""
    assert result.key_question == ""


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


def test_web_evidence_requires_openable_source():
    with pytest.raises(ValidationError, match="requires source_url"):
        EvidenceRecord(
            evidence_id="ev_web",
            agent_id="economist",
            tool_call_id="tool_1",
            claim_summary="房贷利率发生变化",
            source_title="搜索摘要",
            retrieved_at=1.0,
            kind="web_source",
        )


def test_calculation_evidence_requires_inputs_and_formula():
    with pytest.raises(ValidationError, match="calculation evidence"):
        EvidenceRecord(
            evidence_id="ev_calc",
            agent_id="mathematician",
            tool_call_id="tool_2",
            claim_summary="月供约一万元",
            source_title="安全计算结果",
            retrieved_at=1.0,
            kind="calculation",
        )
