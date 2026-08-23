"""决策路径服务单元测试 — PRD §19.1"""
import pytest

from app.playbooks import (
    REAL_ESTATE_PLAYBOOK,
    INVESTMENT_PLAYBOOK,
    CAREER_PLAYBOOK,
    GENERAL_PLAYBOOK,
    select_playbook,
)
from app.schemas.decision_path import AnswerItem
from app.services.decision_path import (
    _build_fallback,
    _build_questions,
    _compute_assumptions,
)


# ============ Playbook 选择 ============

@pytest.mark.parametrize(
    "category, expected",
    [
        ("real_estate", REAL_ESTATE_PLAYBOOK),
        ("offer", CAREER_PLAYBOOK),
        ("job_change", CAREER_PLAYBOOK),
        ("investment", INVESTMENT_PLAYBOOK),
        ("gold", INVESTMENT_PLAYBOOK),
        ("stocks", INVESTMENT_PLAYBOOK),
        (None, GENERAL_PLAYBOOK),
        ("unknown_category", GENERAL_PLAYBOOK),
    ],
)
def test_select_playbook(category, expected):
    assert select_playbook(category) is expected


# ============ 问题选择 ============

def test_build_questions_ids_subset():
    pb = select_playbook("real_estate")
    qs = _build_questions(pb, ["housing_purpose", "holding_horizon"])
    assert len(qs) == 2
    assert {q.id for q in qs} == {"housing_purpose", "holding_horizon"}


def test_build_questions_max_three():
    """硬上限 3 (PRD §6.5 / §9.1)"""
    pb = select_playbook("real_estate")  # real_estate 有 6 个候选
    qs = _build_questions(pb, list(q.id for q in pb.candidate_questions))
    assert len(qs) <= 3


def test_build_questions_invalid_id_skipped():
    pb = select_playbook("real_estate")
    qs = _build_questions(pb, ["housing_purpose", "nonexistent_id"])
    assert len(qs) == 1
    assert qs[0].id == "housing_purpose"


def test_build_questions_empty_uses_fallback_first_three():
    """如果 LLM 什么都没选, 用 playbook 前 3 个候选兜底"""
    pb = select_playbook("real_estate")
    qs = _build_questions(pb, [])
    assert len(qs) >= 1
    assert len(qs) <= 3


def test_every_question_has_unknown_option():
    """PRD §9.3: 每个问题必须含 "不确定" 选项"""
    for pb in (REAL_ESTATE_PLAYBOOK, INVESTMENT_PLAYBOOK, CAREER_PLAYBOOK):
        for tpl in pb.candidate_questions:
            if not tpl.options:
                # short_text 类没有 options, 跳过
                continue
            unknowns = [v for v, _ in tpl.options if v == "unknown"]
            assert unknowns, f"playbook={pb.category} question={tpl.id} 缺 unknown 选项"


# ============ 保守假设计算 ============

def test_compute_assumptions_single_unknown():
    pb = select_playbook("real_estate")
    answers = [
        AnswerItem(question_id="holding_horizon", value="unknown"),
        AnswerItem(question_id="housing_purpose", value="self_live"),
    ]
    assume = _compute_assumptions(pb, answers)
    # 只有 holding_horizon 选了 unknown → 1 个假设
    assert len(assume) == 1
    assert assume[0].question_id == "holding_horizon"


def test_compute_assumptions_multi_includes_unknown():
    pb = select_playbook("investment")
    answers = [
        AnswerItem(question_id="current_assets", value=["cash", "unknown"]),
    ]
    assume = _compute_assumptions(pb, answers)
    assert len(assume) == 1


def test_compute_assumptions_no_unknown_empty():
    pb = select_playbook("real_estate")
    answers = [
        AnswerItem(question_id="holding_horizon", value="5_10"),
        AnswerItem(question_id="housing_purpose", value="self_live"),
    ]
    assert _compute_assumptions(pb, answers) == []


# ============ Fallback 输出 ============

def test_fallback_brief_includes_user_topic():
    pb = select_playbook("real_estate")
    result = _build_fallback(pb, topic="我要不要买房在上海", background="")
    assert "买房" in result["decision_goal"]
    assert result["fallback_used"] is True
    assert len(result["decision_options"]) == len(pb.decision_options)


def test_fallback_is_topic_aware_and_choice_only():
    """兜底不再盲选前 3：看题目内容跳过已写明的，只出选择题，最多 2 个。

    2026-08-23 用户实录：写了"就想知道能不能买房"仍被兜底问
    "想达成的目标"（short_text 打字题）——盲选前 3 的根治。
    """
    pb = select_playbook("investment")
    # 超短话题：只出 ≤2 个选择题
    result = _build_fallback(pb, topic="买黄金", background="")
    ids = result["selected_question_ids"]
    assert 0 < len(ids) <= 2
    picked_types = {q.id: q.answer_type for q in pb.candidate_questions}
    for qid in ids:
        assert picked_types[qid] in ("single_choice", "multi_choice",
                                     "range_choice", "choice_with_custom")

    # 用户已写明目标/期限 → 这些题被跳过
    result2 = _build_fallback(
        pb, topic="想保本增值，这笔钱 5 年内不用，能不能买黄金", background="")
    ids2 = result2["selected_question_ids"]
    assert "investment_goal" not in ids2   # 目标已写
    assert "holding_period" not in ids2    # 期限已写
    # 全写全了 → 0 个问题，直接开会
    result3 = _build_fallback(
        pb,
        topic="想保本增值跑赢通胀，这笔钱 5 年内不用，最多接受 10% 浮亏，"
              "应急金充足，占可支配资金三成",
        background="")
    assert result3["selected_question_ids"] == []


# ============ Playbook 完整性静态校验 ============

def test_each_playbook_has_at_least_3_candidate_questions():
    """PRD §10 每个场景至少 3 个候选问题"""
    for pb in (REAL_ESTATE_PLAYBOOK, INVESTMENT_PLAYBOOK, CAREER_PLAYBOOK, GENERAL_PLAYBOOK):
        assert len(pb.candidate_questions) >= 3, pb.category


def test_each_playbook_has_keywords_for_real_career_investment():
    """3 大场景必须有 keywords 用于 unknown→playbook 路由(不强制 general)"""
    for pb in (REAL_ESTATE_PLAYBOOK, CAREER_PLAYBOOK, INVESTMENT_PLAYBOOK):
        assert len(pb.keywords) > 0, pb.category
