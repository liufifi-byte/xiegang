"""测试 spec v1.0 §4.1 四段公开回复契约的渲染。

V3 重写：旧 6 段渲染（判断/依据/风险/未知/反转/行动）已被替换为
【当前判断】【{lens}】【建议行动】(可选)【关键问题】(可选) 四段契约。
"""
from app.agents.schemas import ExpertResearchResult
from app.graph.nodes.parallel_research import _render_public_research_statement


def _make_result(**overrides):
    base = dict(
        agent_id="economist",
        stance="cautious",
        confidence=0.7,
        judgment="当前更适合继续了解机会，而不是立即承诺。",
        lens_title="成本与回报",
        analysis_points=["留任收益含稳定现金流；跳槽收益依赖尚未确认的股权条款。"],
        action="先取得书面股权条件再比较。",
        key_question="如果股权最终价值为零，你还愿意接受这份工作吗？",
        reversal_condition="书面条件显示现金与积累明显优于留任。",
        evidence_refs=[],
        assumptions=["股权条款尚未确认"],
        degraded=False,
        degradation_reason=None,
    )
    base.update(overrides)
    return ExpertResearchResult(**base)


def test_renders_four_section_contract():
    """四段契约按 spec §4.1 顺序出现。"""
    out = _render_public_research_statement(_make_result(), [])
    assert "【当前判断" in out
    assert "【成本与回报】" in out          # lens_title 作为段名
    assert "【建议行动】" in out
    assert "【关键问题】" in out
    # 顺序
    assert out.index("【当前判断") < out.index("【成本与回报】")
    assert out.index("【成本与回报】") < out.index("【建议行动】")
    assert out.index("【建议行动】") < out.index("【关键问题】")


def test_optional_sections_omitted_when_empty():
    """action/key_question 为空 → 整段省略（spec：不强行凑满）。"""
    out = _render_public_research_statement(
        _make_result(action="", key_question=""), []
    )
    assert "【建议行动】" not in out
    assert "【关键问题】" not in out
    assert "【当前判断" in out
    assert "【成本与回报】" in out


def test_stance_labels_use_new_enum():
    """V3 stance 枚举中文标签。"""
    for stance, label in [
        ("support", "建议推进"), ("oppose", "不建议"),
        ("cautious", "谨慎推进"), ("neutral", "暂不判断"),
    ]:
        out = _render_public_research_statement(
            _make_result(stance=stance), []
        )
        assert label in out, f"{stance} 应显示 {label}"


def test_silent_result_renders_empty():
    """should_speak=false → 一律空串不发布（spec §13.3 不发布伪观点）。"""
    out = _render_public_research_statement(
        _make_result(should_speak=False, judgment="低相关", analysis_points=[]), []
    )
    assert out == ""


def test_no_system_words_in_output():
    """渲染结果不得包含任何系统词（sanitizer + 契约共同保证）。"""
    out = _render_public_research_statement(_make_result(), [])
    for word in ("预算", "超时", "降级", "独立判断", "可核验来源"):
        assert word not in out, f"系统词「{word}」泄漏"


def test_no_evidence_text_inline():
    """证据原文不进正文——正文只有 judgment 与自有分析。"""
    out = _render_public_research_statement(_make_result(), [])
    # 证据区字段根本不渲染（依据由前端可展开区显示）
    assert "网页来源" not in out
    assert "[ev_" not in out


def test_legacy_stance_still_labels():
    """历史数据（for/against）重放时标签兼容（bypass schema 直接测渲染）。"""
    from types import SimpleNamespace
    legacy = SimpleNamespace(
        should_speak=True, stance="for", judgment="历史数据重放。",
        lens_title="成本与回报", analysis_points=[], action="",
        key_question="", evidence_refs=[],
    )
    out = _render_public_research_statement(legacy, [])
    assert "支持" in out
