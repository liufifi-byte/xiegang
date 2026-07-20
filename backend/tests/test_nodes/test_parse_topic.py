"""parse_topic 节点测试。"""
from app.graph.nodes.parse_topic import parse_topic, PARSE_PROMPT, VALID_CATEGORIES


def test_parse_prompt_contains_inputs():
    prompt = PARSE_PROMPT.format(
        topic="买房", background="深圳", category_hint="real_estate"
    )
    assert "买房" in prompt
    assert "深圳" in prompt
    assert "real_estate" in prompt


def test_parse_topic_returns_required_fields(monkeypatch):
    """Mock LLM 调用，验证返回结构。"""
    from app.graph.nodes import parse_topic as pt_module

    class MockBriefing:
        category = "real_estate"
        key_variables = ["city", "price", "down_payment"]
        summary = "深圳购房决策"

    class MockStructuredLLM:
        def invoke(self, prompt):
            return MockBriefing()

    monkeypatch.setattr(
        pt_module, "get_structured_llm",
        lambda schema, temperature=0.5: MockStructuredLLM(),
    )

    state = {
        "topic": "深圳买房",
        "user_background": "工作 5 年",
        "topic_category": None,
    }
    result = parse_topic(state)
    assert result["category"] == "real_estate"
    assert "city" in result["briefing"]["key_variables"]
    assert result["briefing"]["summary"] == "深圳购房决策"


def test_parse_topic_falls_back_to_general_on_invalid_category(monkeypatch):
    """LLM 偶尔乱填 category，强转 general。"""
    from app.graph.nodes import parse_topic as pt_module

    class MockBriefing:
        category = "not_a_real_category"  # 不合法
        key_variables = ["x"]
        summary = "test"

    class MockStructuredLLM:
        def invoke(self, prompt):
            return MockBriefing()

    monkeypatch.setattr(
        pt_module, "get_structured_llm",
        lambda schema, temperature=0.5: MockStructuredLLM(),
    )

    state = {"topic": "test", "user_background": None, "topic_category": None}
    result = parse_topic(state)
    assert result["category"] == "general"
    assert "general" in VALID_CATEGORIES
