"""测试 _render_public_research_statement 从 6 段压缩到 4 段，
关键信息不丢失，旧标题不再出现。
"""
from app.agents.schemas import ExpertResearchResult
from app.graph.nodes.parallel_research import _render_public_research_statement


def _make_result(**overrides):
    base = dict(
        agent_id="mathematician",
        stance="against",
        confidence=0.7,
        thesis="当前投入占可用资金比例过高，机会成本不可忽略。",
        key_risks=["需要用钱时被迫退出", "对手方激励不匹配"],
        assumptions=["未来一年收入保持稳定"],
        unknowns=["是否另有应急储备"],
        reversal_conditions=["投入比例明显下降", "机会成本可证伪"],
        next_actions=["列出未来十二个月的大额支出"],
        degraded=False,
        degradation_reason=None,
    )
    base.update(overrides)
    return ExpertResearchResult(**base)


_EVIDENCE = [
    {
        "evidence_id": "ev_1",
        "kind": "user_fact",
        "supports_claim": "用户计划投入一半可用资金",
        "source_title": "用户提供",
    },
    {
        "evidence_id": "ev_2",
        "kind": "web_source",
        "supports_claim": "近 3 个月指数回报约 8%",
        "source_title": "网页来源",
    },
]


def test_render_has_exactly_four_section_headers():
    """渲染后应只有 4 个【】段标题，不是原来的 6 段。"""
    out = _render_public_research_statement(_make_result(), _EVIDENCE)
    sections = [line for line in out.split("\n\n") if line.startswith("【")]
    assert len(sections) == 4


def test_render_drops_old_section_headers():
    """旧版本 5 个段标题应不再出现。"""
    out = _render_public_research_statement(_make_result(), _EVIDENCE)
    for stale in (
        "【我最担心的地方】",
        "【当前假设与未知】",
        "【你下一步应该做什么】",
    ):
        assert stale not in out, f"旧标题未清理: {stale}"


def test_render_keeps_four_new_headers():
    """新版本 4 个段标题应正确出现。"""
    out = _render_public_research_statement(_make_result(), _EVIDENCE)
    for header in (
        "【我的判断】",
        "【最关键的依据】",
        "【风险与未知】",
        "【什么时候我会改变判断】",
    ):
        assert header in out, f"新标题缺失: {header}"


def test_render_merges_assumptions_into_evidence():
    """assumptions 被并入【最关键的依据】段（标注'核心假设'）。"""
    out = _render_public_research_statement(_make_result(), _EVIDENCE)
    assert "核心假设" in out
    # 确保合并而不是独立段
    assert "未来一年收入保持稳定" in out


def test_render_merges_risks_and_unknowns_into_one():
    """key_risks + unknowns 合并到【风险与未知】。"""
    out = _render_public_research_statement(_make_result(), _EVIDENCE)
    # 两类内容都应在【风险与未知】段附近
    risk_section = out.split("【风险与未知】")[1]
    assert "需要用钱时被迫退出" in risk_section
    assert "仍需确认" in risk_section
    assert "是否另有应急储备" in risk_section


def test_render_works_without_evidence_records():
    """没有 evidence 时也不应崩，fallback 文案应在。"""
    out = _render_public_research_statement(_make_result(evidence_refs=[]), [])
    assert "本轮没有获得可核验的外部来源" in out


def test_render_handles_degraded_result():
    """degraded=True 时 degradation_reason 应并入 unknowns。"""
    out = _render_public_research_statement(
        _make_result(
            degraded=True,
            degradation_reason="超时强制 finalize",
        ),
        _EVIDENCE,
    )
    assert "超时强制 finalize" in out


def test_render_next_actions_field_dropped_but_other_fields_remain():
    """next_actions 字段没出现在输出（移到 summary 阶段），
    但 thesis / risks / unknowns / reversal 仍在。
    """
    out = _render_public_research_statement(_make_result(), _EVIDENCE)
    # next_actions 已不在线性正文里展示
    assert "列出未来十二个月的大额支出" not in out
    # 其他关键字段仍在
    assert "机会成本不可忽略" in out  # thesis
    assert "投入比例明显下降" in out  # reversal_conditions
