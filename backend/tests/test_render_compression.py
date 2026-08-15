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
    """新版本段标题（P3 后判断段带视角签名后缀，用包含匹配）。"""
    out = _render_public_research_statement(_make_result(), _EVIDENCE)
    assert "【我的判断" in out          # · 视角签名跟在后面
    assert "【关键依据】" in out
    assert "【主要风险】" in out
    assert "【什么时候我会改变判断】" in out


def test_render_merges_assumptions_into_evidence():
    """assumptions 被并入【关键依据】段（标注'核心假设'）。"""
    out = _render_public_research_statement(_make_result(), _EVIDENCE)
    assert "核心假设" in out
    # 确保合并而不是独立段
    assert "未来一年收入保持稳定" in out


def test_render_merges_risks_and_unknowns_into_one():
    """key_risks + unknowns 合并到【主要风险】。"""
    out = _render_public_research_statement(_make_result(), _EVIDENCE)
    risk_section = out.split("【主要风险】")[1]
    assert "需要用钱时被迫退出" in risk_section
    assert "仍需确认" in risk_section
    assert "是否另有应急储备" in risk_section


def test_render_works_without_evidence_records():
    """P2 后：0 证据不再写免责声明句——证据段直接显示核心假设。"""
    out = _render_public_research_statement(_make_result(evidence_refs=[]), [])
    # 免责句已删（用户体感差、零信息量）
    assert "本轮没有获得可核验的外部来源" not in out
    # 假设仍在（信息量保留）
    assert "核心假设" in out


def test_render_handles_degraded_result():
    """degraded=True 时：内部 reason 不再外露（P0 修复目的），
    用户看到的是固定的"本轮分析受限"提示。
    """
    out = _render_public_research_statement(
        _make_result(
            degraded=True,
            degradation_reason="超时强制 finalize",
        ),
        _EVIDENCE,
    )
    # 内部错误码/系统术语不得出现在用户可见文案
    assert "超时强制" not in out
    assert "预算" not in out
    assert "上限" not in out
    # 固定降级提示应在
    assert "本轮分析受限" in out


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
