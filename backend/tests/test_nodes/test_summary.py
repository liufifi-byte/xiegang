"""summary 节点测试。"""
import json

from app.graph.nodes.summary import (
    _extract_json_from_response,
    _fallback_summary,
    run_summary,
)


def test_extract_json_plain():
    text = '{"a": 1, "b": [1, 2]}'
    result = _extract_json_from_response(text)
    assert result == {"a": 1, "b": [1, 2]}


def test_extract_json_with_markdown_fence():
    text = '```json\n{"a": 1}\n```'
    result = _extract_json_from_response(text)
    assert result == {"a": 1}


def test_extract_json_with_surrounding_text():
    text = '好的，这是结果：\n{"consensus": ["x"], "y": 2}\n以上。'
    result = _extract_json_from_response(text)
    assert result["consensus"] == ["x"]


def test_extract_json_raises_on_no_json():
    import pytest
    with pytest.raises(ValueError):
        _extract_json_from_response("完全不是 JSON 的纯文本")


def test_fallback_summary_has_required_fields():
    state = {
        "members": {
            "suan": {"stance": "for", "reasoning_summary": "x"},
            "chen": {"stance": "against", "content": "y"},
        }
    }
    result = _fallback_summary(state, "test error")
    assert result["blind_spots"]
    assert "test error" in result["advice"] or "失败" in result["advice"]


def test_run_summary_parses_llm_response(monkeypatch):
    """Mock LLM 返回合法 JSON → 解析成功。"""
    from app.graph.nodes import summary as sm_module

    valid_json = json.dumps({
        "consensus": ["5 议员都认为是高方差决策"],
        "dissent": [{"topic": "时间窗口", "sides": {"ji": "短", "chen": "长"}}],
        "blind_spots": ["没人讨论配偶职业"],
        "advice": "综合建议：分仓试错。这是综合建议，不是正确答案。",
        "open_questions": ["12 个月现金流？"],
    })

    class MockLLM:
        def invoke(self, prompt):
            class R:
                content = valid_json
            return R()

    monkeypatch.setattr(
        sm_module, "get_llm",
        lambda temperature=0.5, max_tokens=2000: MockLLM(),
    )

    state = {
        "topic": "要不要接 offer",
        "election_result": {"winner": "chen", "fallback_used": False},
        "members": {
            "suan": {"stance": "for", "confidence": 0.7, "reasoning_summary": "数字可以"},
            "chen": {"stance": "against", "confidence": 0.8, "reasoning_summary": "风险大"},
        },
        "transcript": [
            {"speaker": "suan", "round": 1, "type": "debate",
             "content": "数字支持", "stance": "for"},
        ],
    }
    result = run_summary(state)
    summary = result["summary"]
    assert summary["consensus"] == ["5 议员都认为是高方差决策"]
    assert summary["chair"] == "chen"
    assert summary["chair_name"] == "老沉"
    assert summary["fallback_used"] is False
    assert "不是正确答案" in summary["advice"]


def test_run_summary_appends_disclaimer_if_missing(monkeypatch):
    """LLM 偶尔忘记写'不是正确答案'，节点要补上。"""
    from app.graph.nodes import summary as sm_module

    valid_json = json.dumps({
        "consensus": ["x"],
        "dissent": [],
        "blind_spots": ["y"],
        "advice": "建议做 X",
        "open_questions": [],
    })

    class MockLLM:
        def invoke(self, prompt):
            class R:
                content = valid_json
            return R()

    monkeypatch.setattr(
        sm_module, "get_llm",
        lambda temperature=0.5, max_tokens=2000: MockLLM(),
    )

    state = {
        "topic": "x",
        "election_result": {"winner": "fan", "fallback_used": True},
        "members": {},
        "transcript": [],
    }
    result = run_summary(state)
    assert "不是正确答案" in result["summary"]["advice"]
    assert result["summary"]["fallback_used"] is True


def test_run_summary_falls_back_on_bad_response(monkeypatch):
    """LLM 返回乱七八糟 → fallback。"""
    from app.graph.nodes import summary as sm_module

    class MockLLM:
        def invoke(self, prompt):
            class R:
                content = "完全不是 JSON"
            return R()

    monkeypatch.setattr(
        sm_module, "get_llm",
        lambda temperature=0.5, max_tokens=2000: MockLLM(),
    )

    state = {
        "topic": "x",
        "election_result": {"winner": "fan"},
        "members": {"suan": {"stance": "for", "reasoning_summary": "x"}},
        "transcript": [],
    }
    result = run_summary(state)
    assert "未能生成" in result["summary"]["advice"] or "test error" in result["summary"]["advice"]
    assert result["summary"]["chair"] == "fan"


def test_run_summary_uses_professional_chair_name(monkeypatch):
    from app.graph.nodes import summary as sm_module

    valid_json = json.dumps({
        "consensus": ["x"],
        "dissent": [],
        "blind_spots": ["y"],
        "advice": "这是综合建议，不是正确答案",
        "open_questions": [],
    })

    class MockLLM:
        def invoke(self, prompt):
            class R:
                content = valid_json
            return R()

    monkeypatch.setattr(sm_module, "get_llm", lambda **kwargs: MockLLM())
    state = {
        "topic": "x",
        "election_result": {"winner": "economist", "fallback_used": False},
        "selected_expert_ids": [
            "mathematician", "economist", "psychologist", "artist", "politician"
        ],
        "members": {"economist": {"stance": "for", "confidence": 0.7}},
        "transcript": [],
    }
    result = run_summary(state)
    assert result["summary"]["chair"] == "economist"
    assert result["summary"]["chair_name"] == "经济学家"


def test_summary_archives_safe_agent_research_metadata(monkeypatch):
    from app.graph.nodes import summary as sm_module

    class BrokenLLM:
        def invoke(self, prompt):
            raise RuntimeError("use fallback")

    monkeypatch.setattr(sm_module, "get_llm", lambda **kwargs: BrokenLLM())
    state = {
        "topic": "x",
        "election_result": {"winner": "economist"},
        "selected_expert_ids": ["economist"],
        "expert_selection_reasons": {"economist": "机会成本"},
        "agent_results": {"economist": {"result": {"thesis": "x"}}},
        "research_degraded": False,
        "members": {},
        "transcript": [],
    }
    summary = run_summary(state)["summary"]
    assert summary["selected_expert_ids"] == ["economist"]
    assert summary["agent_results"]["economist"]["result"]["thesis"] == "x"
