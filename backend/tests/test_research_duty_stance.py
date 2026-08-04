"""测试 DUTY_STANCE_HINT + DUTY_DESIRED_STANCE 映射（防止 5-专家扎堆同立场）。"""
import pytest

from app.agents.profiles import (
    DUTY_DESIRED_STANCE,
    DUTY_STANCE_HINT,
    PARLIAMENT_DUTIES,
    assign_parliament_duties,
)


def test_every_duty_has_stance_hint():
    """每个议会职责都应该有对应的立场倾向 hint（注释+字符串）。"""
    for duty in PARLIAMENT_DUTIES:
        assert duty in DUTY_STANCE_HINT, f"{duty} 缺少 stance hint"
        assert len(DUTY_STANCE_HINT[duty]) > 10, f"{duty} 的 hint 太短"


def test_every_duty_has_desired_stance():
    """每个职责都应该有期望的 stance（用于重跑时同步给 LLM 做强制）。"""
    for duty in PARLIAMENT_DUTIES:
        assert duty in DUTY_DESIRED_STANCE, f"{duty} 缺少 desired stance"
        assert DUTY_DESIRED_STANCE[duty] in ("for", "against", "neutral")


def test_desired_stances_are_diverse():
    """关键不变量：5 个职责的 desired stance 应天然分散到至少 2 个不同方向，
    避免所有专家扎堆 → 重跑也无法救场。
    """
    stances = set(DUTY_DESIRED_STANCE.values())
    assert len(stances) >= 2, "职责期望立场完全不分散，会加剧扎堆"


def test_assign_parliament_duties_full_coverage():
    """5 个专家的议会职责分配应该覆盖到全部 5 个职责。"""
    duties = assign_parliament_duties(
        ["mathematician", "historian", "economist", "psychologist", "politician"],
        category="real_estate",
    )
    assert len(duties) == 5
    assert set(duties.values()) == set(PARLIAMENT_DUTIES)


def test_duty_stance_hint_present_in_format():
    """DUTY_STANCE_HINT 必须包含职责相关 + 中文提示词，方便注入到 prompt。"""
    assert "下行风险" in DUTY_STANCE_HINT["downside_risk"]
    assert "支持" in DUTY_STANCE_HINT["opportunity_scan"]
    assert "反对" in DUTY_STANCE_HINT["contrarian_challenge"]
    assert "暂不作绝对判断" in DUTY_STANCE_HINT["evidence_audit"]
