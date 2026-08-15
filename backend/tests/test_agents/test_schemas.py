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


# ============================================================
# AgentDecision.repair_envelope：GLM 结构化输出事故回归
# （真实案例：议会 45577003 心理学家，action 被填成中文说明句、
#   ExpertResearchResult 字段平铺在顶层 → literal_error → 专家整场沉默）
# ============================================================

# 事故原文形态（截取自 degradation_reason 里的 raw_content）
_PSYCHOLOGIST_FLAT = {
    "lens_title": "动机与冲突",
    "judgment": "谨慎推进。成长动机清晰，但需警惕用能力自信掩盖竞业封锁的现实风险。",
    "analysis_points": [
        "用户认为技能抢手回市场不难，但这可能是在用个人能力自信防御竞业风险焦虑。"
    ],
    "action": "建议进行一次防御性验证，确认退路的真实性。",
}


def test_agent_decision_repairs_flattened_result_payload():
    """结论字段平铺 + action 是说明句 → 包装成 finish，内容不丢。"""
    decision = AgentDecision.model_validate(dict(_PSYCHOLOGIST_FLAT))
    assert decision.action == "finish"
    assert decision.result is not None
    assert decision.result.lens_title == "动机与冲突"
    assert "能力自信" in decision.result.judgment
    assert len(decision.result.analysis_points) == 1
    # 缺失的必填字段有兜底值
    assert decision.result.agent_id == ""
    assert decision.result.stance == "neutral"
    assert decision.result.confidence == 0.3


def test_agent_decision_repairs_sentence_action_with_nested_result():
    """action 是说明句但 result 已嵌套 → 只纠正 action。"""
    decision = AgentDecision.model_validate(
        {
            "action": "信息已足够，直接给出最终结论。",
            "result": _result().model_dump(),
        }
    )
    assert decision.action == "finish"
    assert decision.result.agent_id == "economist"


def test_agent_decision_repairs_sentence_action_with_tool_request():
    """action 是说明句但带 tool_request → 纠正为 tool。"""
    decision = AgentDecision.model_validate(
        {
            "action": "需要先核实竞业协议的赔偿条款",
            "tool_request": {
                "tool_name": "web_search",
                "arguments": {"query": "竞业协议 赔偿 判例"},
                "public_intent": "verify_public_fact",
            },
        }
    )
    assert decision.action == "tool"
    assert decision.tool_request.tool_name == "web_search"


def test_agent_decision_valid_envelope_untouched():
    """合法输入不经修复器改写（action 合法时原样返回）。"""
    raw = {"action": "tool", "tool_request": None, "result": None, "extra": 1}
    # action 合法但 tool_request 缺失 → 仍按原逻辑报错（修复器不越权）
    with pytest.raises(ValidationError):
        AgentDecision.model_validate(raw)


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
