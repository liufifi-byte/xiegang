from app.graph.nodes.clarification_gate import (
    ClarificationQuestion,
    _clean_questions,
    assess_clarification_need,
    route_after_clarification_gate,
    route_after_clarification_persist,
)


def question(
    question_id: str,
    text: str,
    *,
    answer_type: str = "single_choice",
    options: list[str] | None = None,
) -> ClarificationQuestion:
    return ClarificationQuestion(
        id=question_id,
        question=text,
        why_it_matters="这个答案可能改变最终建议。",
        answer_type=answer_type,
        options=options or ["低于20%", "20%以上"],
        impact="high",
        source="user_only",
    )


def test_clean_questions_keeps_only_user_answerable_high_impact_items():
    cleaned = _clean_questions(
        [
            question("allocation", "这笔投入占你可支配资金的多少？"),
            question("price", "当前价格是多少？"),
            question("vague", "还有什么需要补充的吗？"),
            question("allocation", "重复问题"),
        ]
    )
    assert [item.id for item in cleaned] == ["allocation"]
    assert cleaned[0].options[-1] == "不确定"


def test_clean_questions_removes_options_from_short_text():
    cleaned = _clean_questions(
        [
            question(
                "goal",
                "你最希望这次选择解决什么问题？",
                answer_type="short_text",
            )
        ]
    )
    assert cleaned[0].options == []


def test_gate_does_not_run_twice(monkeypatch):
    monkeypatch.setattr(
        "app.graph.nodes.clarification_gate.get_structured_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("LLM should not be called")
        ),
    )
    result = asyncio.run(
        assess_clarification_need(
            {
                "clarification_status": "skipped",
                "clarification_questions": [{"id": "old"}],
            }
        )
    )
    assert result["clarification_status"] == "skipped"


def test_clarification_routes_wait_or_research():
    assert (
        route_after_clarification_gate(
            {
                "clarification_status": "awaiting_input",
                "clarification_questions": [{"id": "q1"}],
            }
        )
        == "awaiting_input"
    )
    assert (
        route_after_clarification_gate(
            {"clarification_status": "not_needed", "clarification_questions": []}
        )
        == "research"
    )
    assert route_after_clarification_persist({"clarification_waiting": True}) == "wait"
    assert route_after_clarification_persist({}) == "research"
import asyncio


# ============================================================
# P0 修复：setup 两步流（decision path）确认过的议会不再重复追问
# ============================================================

import asyncio


def test_confirmed_decision_path_skips_clarification():
    """用户刚在 setup 两步流确认过决策路径 → 不应再弹追问（重复弹窗 bug）。"""
    state = {
        "topic": "要不要跳槽",
        "user_background": "字节 P7",
        "clarification_status": "not_checked",
        "decision_path_status": "confirmed",
        "decision_path": {
            "goal": "评估跳槽风险",
            "answers": [{"label": "收入变化", "value": "未知"}],
        },
    }
    result = asyncio.run(assess_clarification_need(state))
    assert result["clarification_status"] == "not_needed"
    assert result["clarification_questions"] == []
    assert "两步流" in result["clarification_reason"]
    # 路由应直接去 research（不进 persist_clarification）
    assert route_after_clarification_gate({**state, **result}) == "research"


def test_legacy_parliament_without_decision_path_still_asks():
    """legacy 入口（没走 setup 两步流）→ 保持原行为，允许追问。"""
    called = {"llm": False}

    async def fake_invoke(self, prompt, **kw):
        called["llm"] = True
        return ClarificationDecision(
            needs_clarification=True,
            reason="缺关键信息",
            questions=[question("income", "新工作收入变化多少？")],
        )

    from app.graph.nodes import clarification_gate as cg
    from app.llm import _StructuredWrapper
    orig = _StructuredWrapper.ainvoke
    _StructuredWrapper.ainvoke = fake_invoke
    try:
        state = {
            "topic": "要不要跳槽",
            "user_background": "",
            "clarification_status": "not_checked",
            "decision_path_status": "legacy",
            "decision_path": {},
        }
        result = asyncio.run(assess_clarification_need(state))
    finally:
        _StructuredWrapper.ainvoke = orig
    assert called["llm"] is True
    assert result["clarification_status"] == "awaiting_input"
    assert len(result["clarification_questions"]) >= 1


def test_unconfirmed_decision_path_does_not_skip():
    """decision_path 存在但未确认（draft/过期）→ 不跳过，仍走正常评估。"""
    state = {
        "topic": "要不要跳槽",
        "user_background": "",
        "clarification_status": "not_checked",
        "decision_path_status": "not_started",  # 非 confirmed
        "decision_path": {},
    }
    # 不做 LLM mock——只要不因 not_needed 提前 return 即可；
    # 用异常 LLM 让它走 fallback 分支验证未被跳过
    result = asyncio.run(assess_clarification_need(state))
    # fallback 分支：要么 awaiting_input（有问题）要么 not_needed（无问题），
    # 但 reason 不会是"两步流"——证明没走 skip 分支
    assert "两步流" not in (result.get("clarification_reason") or "")
