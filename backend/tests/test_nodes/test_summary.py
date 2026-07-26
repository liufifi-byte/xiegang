import json

import pytest

from app.graph.nodes.summary import (
    _extract_json_from_response,
    _fallback_summary,
    run_summary,
)


def _state(**overrides):
    state = {
        "topic": "要不要买房",
        "decision_model": {
            "question": "要不要买房",
            "user_facts": ["家庭税后月收入4万元"],
            "unknowns": ["未来三年是否换城市"],
            "research_questions": ["核实当地租售比"],
        },
        "selected_expert_ids": ["mathematician", "economist"],
        "expert_selection_reasons": {},
        "members": {
            "mathematician": {
                "stance": "neutral",
                "content": "现金流阈值尚未确认",
                "basis_level": "moderate",
            },
            "economist": {
                "stance": "against",
                "content": "应比较继续租住的机会成本",
                "basis_level": "limited",
            },
        },
        "transcript": [
            {
                "speaker": "economist",
                "round": 2,
                "type": "debate",
                "content": "应先验证持有期限。",
            }
        ],
        "core_disputes": [
            {
                "dispute_id": "d1",
                "question": "流动性成本是否可接受？",
                "participants": ["mathematician", "economist"],
            }
        ],
        "agent_results": {},
        "current_round": 2,
    }
    state.update(overrides)
    return state


def test_extract_json_tolerates_markdown_and_surrounding_text():
    assert _extract_json_from_response('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json_from_response('结果：{"a": 2}。') == {"a": 2}
    with pytest.raises(ValueError):
        _extract_json_from_response("没有 JSON")


def test_fallback_summary_is_actionable_without_internal_error_text():
    result = _fallback_summary(_state())
    assert result["recommendation"]
    assert result["reversal_conditions"]
    assert "TimeoutError" not in json.dumps(result, ensure_ascii=False)


def test_run_summary_is_neutral_and_excludes_agent_work_paths(monkeypatch):
    from app.graph.nodes import summary as sm_module

    payload = {
        "recommendation": "暂缓购买，先完成两项验证。",
        "recommendation_strength": "conditional",
        "key_reason": "持有期限尚未确认。",
        "consensus": ["先验证现金流"],
        "dissent": [],
        "decision_options": [],
        "assumptions": ["未来三年可能换城市"],
        "next_actions": ["计算三年现金流"],
        "reversal_conditions": ["确认居住五年以上"],
        "open_questions": ["是否换城市"],
    }

    class MockLLM:
        def invoke(self, prompt):
            class Response:
                content = json.dumps(payload, ensure_ascii=False)

            return Response()

    monkeypatch.setattr(sm_module, "get_llm", lambda **kwargs: MockLLM())
    summary = run_summary(_state())["summary"]
    assert summary["recommendation"] == payload["recommendation"]
    assert summary["chair"] is None
    assert summary["chair_name"] == "中立主持人"
    assert summary["round_count"] == 2
    assert "agent_results" not in summary
    assert "agent_traces" not in summary
    assert "tool_calls" not in summary


def test_summary_groups_four_evidence_kinds(monkeypatch):
    from app.graph.nodes import summary as sm_module

    class BrokenLLM:
        def invoke(self, prompt):
            raise TimeoutError()

    monkeypatch.setattr(sm_module, "get_llm", lambda **kwargs: BrokenLLM())
    state = _state(
        agent_results={
            "mathematician": {
                "result": {"assumptions": ["房价保持不变"]},
                "evidence": [
                    {
                        "evidence_id": "web_1",
                        "kind": "web_source",
                        "claim_summary": "当前房贷利率",
                        "source_title": "官方页面",
                        "source_url": "https://example.com/rate",
                    },
                    {
                        "evidence_id": "calc_1",
                        "kind": "calculation",
                        "claim_summary": "月供计算",
                        "source_title": "安全计算结果",
                        "calculation_inputs": {"principal": 3000000},
                        "calculation_formula": "principal * rate",
                    },
                ],
            }
        }
    )
    summary = run_summary(state)["summary"]
    basis = summary["analysis_basis"]
    assert basis["user_facts"]
    assert basis["web_sources"][0]["source_url"]
    assert basis["calculations"][0]["calculation_formula"]
    assert basis["inferences"][0]["content"] == "房价保持不变"
