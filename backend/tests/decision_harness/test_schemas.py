"""V4 契约不变量测试（spec §21.1 中 Phase 0 可离线验证的部分）。"""
import pytest
from pydantic import ValidationError

from app.decision_harness.schemas import (
    BudgetPolicy,
    DebateTurnResponse,
    DecisionModel,
    DisputeIssue,
    EvidenceRecordV4,
    ExpertTaskPacket,
    FinalReport,
    MainAgentActionRequest,
    PlanSpec,
    Recommendation,
    RunExitStatus,
    SubAgentResult,
)


def _packet(**overrides):
    base = dict(
        task_id="task_001",
        expert_id="economist",
        objective="比较留任与跳槽两个方案的机会成本与三年现金流差异",
        context_version=1,
        allowed_tools=["search_evidence", "calculator"],
    )
    base.update(overrides)
    return ExpertTaskPacket(**base)


def _dispute(**overrides):
    base = dict(
        dispute_id="dispute_1",
        question="股权条款未确认前是否应接受 Offer",
        importance=0.9,
        conflict_type="assumption_conflict",
        participants=["economist", "strategist"],
    )
    base.update(overrides)
    return DisputeIssue(**base)


# ---------- 主 Agent Action Schema ----------

def test_main_action_rejects_unknown_action():
    with pytest.raises(ValidationError):
        MainAgentActionRequest(action="free_chat_forever")
    req = MainAgentActionRequest(action="start_debate")
    assert req.action.value == "start_debate"


# ---------- ExpertTaskPacket ----------

def test_task_packet_rejects_unknown_expert():
    with pytest.raises(ValidationError, match="未知专家"):
        _packet(expert_id="politician")


def test_task_packet_rejects_generic_objective():
    with pytest.raises(ValidationError):
        _packet(objective="请分析这个问题")
    with pytest.raises(ValidationError):
        _packet(objective="太短")


def test_task_packet_valid_passes():
    packet = _packet()
    assert packet.expert_id == "economist"
    assert packet.context_version == 1


# ---------- SubAgentResult ----------

def test_sub_agent_result_normalizes_legacy_stance():
    result = SubAgentResult.model_validate(
        {
            "task_id": "task_001",
            "agent_id": "economist",
            "stance": "against",
            "judgment": "谨慎推进",
        }
    )
    assert result.stance == "oppose"


def test_silent_result_must_not_fabricate():
    with pytest.raises(ValidationError, match="伪观点"):
        SubAgentResult.model_validate(
            {
                "task_id": "task_001",
                "agent_id": "psychologist",
                "status": "silent",
                "stance": "neutral",
                "judgment": "我认为应该推进",
                "analysis_points": ["理由一"],
            }
        )


def test_partial_result_is_a_first_class_status():
    result = SubAgentResult.model_validate(
        {
            "task_id": "task_001",
            "agent_id": "mathematician",
            "status": "partial",
            "stance": "cautious",
            "judgment": "关键变量缺失，仅给出方向性判断",
        }
    )
    assert result.status == "partial"


# ---------- DisputeIssue ----------

def test_dispute_requires_two_participants():
    with pytest.raises(ValidationError, match="至少需要两位"):
        _dispute(participants=["economist"])


def test_dispute_rejects_low_importance():
    with pytest.raises(ValidationError, match="不值得开辩论"):
        _dispute(importance=0.1)


def test_dispute_rejects_duplicate_and_unknown_participants():
    with pytest.raises(ValidationError, match="不得重复"):
        _dispute(participants=["economist", "economist"])
    with pytest.raises(ValidationError, match="八人库"):
        _dispute(participants=["economist", "artist"])


# ---------- DebateTurnResponse ----------

def test_debate_turn_changed_consistency():
    valid = DebateTurnResponse(
        dispute_id="dispute_1",
        agent_id="economist",
        position_before="cautious",
        position_after="oppose",
        changed=True,
        reason="历史学家的路径锁定证据成立",
    )
    assert valid.changed is True
    with pytest.raises(ValidationError, match="changed=true"):
        DebateTurnResponse(
            dispute_id="dispute_1",
            agent_id="economist",
            position_before="cautious",
            position_after="cautious",
            changed=True,
        )
    with pytest.raises(ValidationError, match="显式 changed"):
        DebateTurnResponse(
            dispute_id="dispute_1",
            agent_id="economist",
            position_before="cautious",
            position_after="oppose",
            changed=False,
        )


# ---------- Evidence ----------

def test_web_evidence_requires_url():
    with pytest.raises(ValidationError, match="source_url"):
        EvidenceRecordV4(id="ev1", claim_id="claim_1", kind="web_source")
    ok = EvidenceRecordV4(
        id="ev1",
        claim_id="claim_1",
        kind="web_source",
        source_url="https://example.com/a",
    )
    assert ok.kind.value == "web_source"


# ---------- Budget ----------

def test_budget_reserves_leave_room_for_experts():
    with pytest.raises(ValidationError, match="挤死"):
        BudgetPolicy(audit_reserve_ratio=0.4, synthesis_reserve_ratio=0.4)
    policy = BudgetPolicy()
    assert policy.max_main_actions == 12
    assert policy.max_replans == 3
    assert policy.max_debate_rounds_per_dispute == 2


# ---------- FinalReport 契约 ----------

def _report(**overrides):
    base = dict(
        recommendation=Recommendation(
            option="暂缓跳槽并谈判",
            strength="conditional",
            summary="先取得书面股权条件再比较",
        ),
        completion={"status": "FINALIZED", "limitations": []},
    )
    base.update(overrides)
    return base


def test_final_report_contract_roundtrip():
    report = FinalReport.model_validate(_report())
    assert report.completion.status is RunExitStatus.FINALIZED
    dumped = report.model_dump(mode="json")
    assert "recommendation" in dumped and "action_plan" in dumped


def test_insufficient_recommendation_must_explain_uncertainty():
    with pytest.raises(ValidationError, match="remaining_uncertainties"):
        FinalReport.model_validate(
            _report(recommendation={"option": "暂不判断", "strength": "insufficient", "summary": "信息不足"})
        )


# ---------- Plan / DecisionModel 基本形状 ----------

def test_plan_and_decision_model_shapes():
    plan = PlanSpec(
        plan_version=1,
        objective="给出跳槽建议和行动顺序",
        expert_tasks=["task_001"],
        created_from_state_version=1,
    )
    assert plan.plan_version == 1
    model = DecisionModel(question="是否跳槽", options=["留任", "跳槽"])
    assert model.risk_level == "medium"
