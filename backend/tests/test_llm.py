"""GLM-5 客户端 + schemas 的单元测试。

注意：这里只测试 schema 验证和客户端构造，不打真实 API。
"""
from app.schemas.member import Evidence, ElectionPitch, MemberStatement


def test_evidence_schema_validates():
    ev = Evidence(claim="test", source="src", strength="high")
    assert ev.strength == "high"


def test_evidence_rejects_invalid_strength():
    import pytest
    with pytest.raises(Exception):
        Evidence(claim="x", source="y", strength="unknown")  # type: ignore


def test_member_statement_schema_validates():
    stmt = MemberStatement(
        member_id="suan",
        stance="for",
        confidence=0.8,
        evidence=[
            Evidence(claim="期望值 5%", source="自计算", strength="high"),
            Evidence(claim="折现后现值 2500 万", source="calculator", strength="high"),
        ],
        reasoning_summary="数字看不合算",
    )
    assert stmt.member_id == "suan"
    assert stmt.stance == "for"
    assert len(stmt.evidence) == 2
    assert 0.7 <= stmt.confidence <= 0.9


def test_member_statement_rejects_confidence_out_of_range():
    import pytest
    with pytest.raises(Exception):
        MemberStatement(
            member_id="x", stance="for", confidence=1.5,
            evidence=[], reasoning_summary="x",
        )


def test_election_pitch_schema_validates():
    pitch = ElectionPitch(
        member_id="suan",
        recommend_target="chen",
        target_specific_view="老沉提到 -83% 回撤",
        target_blind_spot="忽视机会成本",
        own_bias="我太算账忽略人性",
        synthesis_method="我会先列对照表再标依赖",
    )
    assert pitch.recommend_target == "chen"
    assert "83%" in pitch.target_specific_view


def test_get_llm_returns_chat_model():
    """验证 LLM 客户端构造 + 配置正确（不打 API）。"""
    from app.llm import get_llm
    llm = get_llm()
    assert llm.model_name == "glm-5"
    assert "bigmodel.cn" in str(llm.openai_api_base)
    assert llm.request_timeout == 25.0
    assert llm.max_retries == 0


def test_get_structured_llm_returns_wrapper():
    """验证返回的是容错 wrapper（不是裸 Runnable）。"""
    from app.llm import _StructuredWrapper, get_structured_llm
    wrapper = get_structured_llm(MemberStatement)
    assert isinstance(wrapper, _StructuredWrapper)
    assert wrapper.schema is MemberStatement


# ============ extract_json_from_text 测试 ============

def test_extract_json_plain():
    from app.llm import extract_json_from_text
    assert extract_json_from_text('{"a": 1}') == '{"a": 1}'


def test_extract_json_with_markdown_fence():
    """最关键的测试：GLM-5 输出 markdown fence 包 JSON 时能剥出来。"""
    from app.llm import extract_json_from_text
    text = '```json\n{"a": 1, "b": 2}\n```'
    assert extract_json_from_text(text) == '{"a": 1, "b": 2}'


def test_extract_json_with_bare_fence():
    from app.llm import extract_json_from_text
    text = '```\n{"x": 1}\n```'
    assert extract_json_from_text(text) == '{"x": 1}'


def test_extract_json_with_surrounding_text():
    from app.llm import extract_json_from_text
    text = '好的，这是结果：\n{"consensus": ["x"]}\n以上。'
    assert extract_json_from_text(text) == '{"consensus": ["x"]}'


def test_extract_json_raises_on_no_json():
    import pytest
    from app.llm import extract_json_from_text
    with pytest.raises(ValueError):
        extract_json_from_text("纯文本没有 JSON")


def test_extract_json_handles_nested_braces():
    from app.llm import extract_json_from_text
    text = '{"a": {"b": 1}, "c": [1, 2]}'
    assert extract_json_from_text(text) == text


# ============ _StructuredWrapper 测试 ============

def test_structured_wrapper_falls_back_on_fenced_json(monkeypatch):
    """原生 with_structured_output 失败时，wrapper 应该降级 invoke + 手动 parse。

    模拟 GLM-5 包 markdown fence 的输出：
      - self.structured.invoke() 抛异常（模拟原生 parse 失败）
      - self.llm.invoke() 返回包含 ```json ... ``` 的字符串
      - wrapper 应该成功剥出 JSON 并 schema 校验通过
    """
    from app.llm import _StructuredWrapper
    from app.schemas.member import MemberStatement

    fenced = '```json\n{"member_id": "suan", "stance": "for", "confidence": 0.8, "evidence": [{"claim": "x", "source": "y", "strength": "high"}], "reasoning_summary": "ok"}\n```'

    class MockStructured:
        def invoke(self, prompt, **kwargs):
            raise ValueError("原生 parse 失败（模拟 GLM 包了 fence）")

    class MockRawResponse:
        content = fenced

    class MockLLM:
        def with_structured_output(self, schema):
            return MockStructured()
        def invoke(self, prompt, **kwargs):
            return MockRawResponse()

    wrapper = _StructuredWrapper(MemberStatement, temperature=0.5)
    # 替换内部依赖，绕开真实 API
    monkeypatch.setattr(wrapper, "llm", MockLLM())
    monkeypatch.setattr(wrapper, "structured", MockStructured())

    result = wrapper.invoke("任意 prompt")
    assert isinstance(result, MemberStatement)
    assert result.member_id == "suan"
    assert result.stance == "for"
    assert len(result.evidence) == 1


def test_structured_wrapper_uses_native_when_ok(monkeypatch):
    """原生 structured.invoke 成功时直接用，不走 fallback。"""
    from app.llm import _StructuredWrapper

    expected = MemberStatement(
        member_id="chen", stance="against", confidence=0.9,
        evidence=[], reasoning_summary="原生路径",
    )

    class MockStructured:
        def invoke(self, prompt, **kwargs):
            return expected

    class MockLLM:
        def with_structured_output(self, schema):
            return MockStructured()
        def invoke(self, prompt, **kwargs):
            raise AssertionError("不应该走 fallback")

    wrapper = _StructuredWrapper(MemberStatement, temperature=0.5)
    monkeypatch.setattr(wrapper, "llm", MockLLM())
    monkeypatch.setattr(wrapper, "structured", MockStructured())

    result = wrapper.invoke("任意")
    assert result is expected
    assert result.reasoning_summary == "原生路径"
