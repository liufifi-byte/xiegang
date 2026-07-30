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
