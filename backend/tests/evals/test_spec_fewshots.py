"""spec v1.0 §8/§10 Few-shot 回归集。

把规范的合格/不合格输出样例固化为结构断言——确保 schema、渲染、
质量门在样例数据上行为正确。

不调真实 LLM：直接用 spec JSON 样例构造 ExpertResearchResult，
验证 schema 接受合格样例、拒绝不合格样例、渲染符合 §4.1 契约。
"""
import pytest
from pydantic import ValidationError

from app.agents.schemas import ExpertResearchResult
from app.graph.nodes.parallel_research import _render_public_research_statement
from app.graph.nodes.text_sanitizer import quality_gate_issues


# ============================================================
# §8.2 经济学家合格样例（spec JSON 直取）
# ============================================================

ECONOMIST_GOOD = dict(
    agent_id="economist",
    relevance=0.96,
    should_speak=True,
    stance="cautious",
    confidence=0.69,
    judgment="值得继续推进这次机会，但现在还不足以支持正式离职。",
    lens_title="成本与回报",
    analysis_points=[
        "留在成熟公司的收益不只有工资，还包括稳定现金流、平台资源和已有职业积累；跳槽的潜在收益则来自职责扩大、成长速度和股权上行空间。",
        "目前最容易被高估的是尚未明确的股权。只有确认持有方式、归属条件、稀释和退出路径后，它才具备可比较的经济价值。",
    ],
    action="取得完整书面条件后，比较留任、跳槽成功和初创发展不及预期三种情景下未来两年的收入与退出成本。",
    key_question="如果这部分股权最终价值为零，你是否仍然愿意接受这份工作？",
    reversal_condition="书面条件显示现金收入和岗位积累即使不依赖股权也明显优于留任。",
    evidence_refs=[],
    assumptions=["初创公司的具体现金薪酬和股权条款尚未确认"],
)


def test_spec_economist_fewshot_passes_schema():
    """§8.2 样例必须能构造合法 ExpertResearchResult。"""
    result = ExpertResearchResult(**ECONOMIST_GOOD)
    assert result.stance == "cautious"
    assert result.lens_title == "成本与回报"
    assert len(result.analysis_points) == 2


def test_spec_economist_fewshot_renders_contract():
    """§8.2 样例渲染出四段契约，无系统词。"""
    out = _render_public_research_statement(
        ExpertResearchResult(**ECONOMIST_GOOD), []
    )
    assert "【当前判断" in out
    assert "【成本与回报】" in out
    assert "【建议行动】" in out
    assert "【关键问题】" in out
    for word in ("预算", "超时", "降级", "独立判断", "可核验来源"):
        assert word not in out


def test_spec_economist_fewshot_passes_quality_gate():
    """§8.2 样例通过确定性质量门。"""
    r = ExpertResearchResult(**ECONOMIST_GOOD)
    issues = quality_gate_issues(
        judgment=r.judgment,
        analysis_points=r.analysis_points,
        action=r.action,
        key_question=r.key_question,
        lens_title=r.lens_title,
        expected_lens="成本与回报",
    )
    assert issues == [], f"质量门误报: {issues}"


# ============================================================
# §8.3 心理学家合格样例
# ============================================================

PSYCHOLOGIST_GOOD = dict(
    agent_id="psychologist",
    relevance=0.9,
    should_speak=True,
    stance="neutral",
    confidence=0.62,
    judgment="现在最需要确认的不是你能否承受创业风险，而是你真正想离开的究竟是什么。",
    lens_title="动机与冲突",
    analysis_points=[
        "你同时看重成长空间和财务安全，这不是优柔寡断，而是两个真实需求发生了冲突。",
    ],
    action="分别写下你希望通过跳槽获得的三项改变，并检查是否只有加入这家公司才能实现。",
    key_question="如果当前公司明天给你更大的职责范围，你还会想去这家初创公司吗？",
    reversal_condition="用户确认核心诉求是初创岗位独有的工作内容。",
    evidence_refs=[],
    assumptions=[],
)


def test_spec_psychologist_fewshot_passes_all():
    r = ExpertResearchResult(**PSYCHOLOGIST_GOOD)
    out = _render_public_research_statement(r, [])
    assert "【动机与冲突】" in out
    assert "逃避" not in r.judgment  # 不病理化


# ============================================================
# §8.5 法学家合格样例：区分已知条款与未看文件
# ============================================================

LEGAL_GOOD = dict(
    agent_id="legal_scholar",
    relevance=0.95,
    should_speak=True,
    stance="cautious",
    confidence=0.58,
    judgment="在竞业范围和股权文件未核对前，不宜把口头 offer 视为可安全执行的跳槽方案。",
    lens_title="权利与边界",
    analysis_points=[
        "目前只能确认用户签过竞业协议，尚不能据此判断它是否覆盖目标公司；结论必须回到协议原文和适用地区。",
    ],
    action="在提交离职前整理竞业协议和股权文件，由专业人士核对关键条款。",
    key_question="你是否已经拿到竞业协议原文和正式股权授予文件？",
    reversal_condition="原始文件确认目标公司不在限制范围内。",
    evidence_refs=[],
    assumptions=["尚未取得需要审阅的完整原始文件"],
)


def test_spec_legal_fewshot_no_absolute_language():
    """法学家禁止'必然有效/一定败诉'绝对表达（spec §6.6）。"""
    r = ExpertResearchResult(**LEGAL_GOOD)
    full = " ".join([r.judgment, *r.analysis_points])
    for banned in ("必然有效", "一定败诉", "肯定胜诉"):
        assert banned not in full


# ============================================================
# §10.1 低相关不发言（法学家 × 爬山问题）
# ============================================================

def test_spec_low_relevance_silent():
    """relevance<0.35 → should_speak=false → 渲染空（不生成伪观点）。"""
    silent = ExpertResearchResult(
        agent_id="legal_scholar",
        relevance=0.12,
        should_speak=False,
        stance="neutral",
        confidence=0.9,
        judgment="低相关",
        lens_title="权利与边界",
        analysis_points=[],
    )
    out = _render_public_research_statement(silent, [])
    assert out == ""


def test_spec_silent_with_long_opinion_rejected():
    """should_speak=false 却生成完整观点 → schema 拒绝（validator）。"""
    with pytest.raises(ValidationError):
        ExpertResearchResult(
            agent_id="legal_scholar",
            relevance=0.12,
            should_speak=False,
            stance="neutral",
            confidence=0.9,
            judgment="从法律角度看爬山可能涉及景区责任和意外事故，因此建议在家看电影。这个长度刚好超过二十个汉字用来触发 should_speak=false 的 schema 校验拒绝机制。",
            lens_title="权利与边界",
            analysis_points=[],
        )


# ============================================================
# §10.2 搜索失败降级：不出现系统说明
# ============================================================

def test_spec_search_failure_degradation_no_system_words():
    """降级输出（_evidence_backed_fallback 两级）均不得含系统说明。"""
    from app.agents.research_graph import _evidence_backed_fallback
    from app.agents.profiles import get_expert_profile
    from app.agents.harness import AgentHarness
    from app.agents.tool_registry import ToolContext, ToolRegistry
    from app.agents.trace import AgentTraceSink

    profile = get_expert_profile("economist")
    harness = AgentHarness(
        parliament_id="p-fewshot",
        profile=profile,
        registry=ToolRegistry(),
        context=ToolContext(profile.expert_id, None, "general"),
        trace_sink=AgentTraceSink("p-fewshot", writer=lambda *_: 0),
    )
    # 无证据级
    r = _evidence_backed_fallback(profile, harness, "tool timeout simulated")
    out = _render_public_research_statement(r, [])
    for banned in (
        "受预算限制", "请与其他专家交叉比对", "没有可核验来源",
        "预算", "超时", "方向性参考",
    ):
        assert banned not in out, f"降级输出泄漏系统词「{banned}」: {out}"


# ============================================================
# §4.2 字数与条数限制（schema 边界）
# ============================================================

def test_judgment_over_200_chars_rejected():
    with pytest.raises(ValidationError):
        ExpertResearchResult(**{**ECONOMIST_GOOD, "judgment": "长" * 201})


def test_four_analysis_points_rejected():
    with pytest.raises(ValidationError):
        ExpertResearchResult(**{
            **ECONOMIST_GOOD,
            "analysis_points": ["观测一" * 20, "观测二" * 20, "观测三" * 20, "观测四" * 20],
        })


def test_quality_gate_catches_multi_question_marks():
    issues = quality_gate_issues(key_question="第一个问题？第二个问题？")
    assert any("多个问号" in i for i in issues)


def test_quality_gate_catches_lens_mismatch():
    issues = quality_gate_issues(
        judgment="ok", lens_title="路径与规律", expected_lens="成本与回报"
    )
    assert any("lens" in i for i in issues)
