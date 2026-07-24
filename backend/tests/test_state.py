"""ParliamentState 基础测试。"""
from app.graph.state import ParliamentState, make_initial_state


def test_make_initial_state_has_all_fields():
    state = make_initial_state(topic="要不要买房", parliament_id="p-1")
    # 所有 TypedDict 字段都必须有默认值
    required_keys = {
        "topic", "user_id", "topic_category", "user_background",
        "briefing", "category", "members",
        "current_round", "max_rounds", "tension_score",
        "transcript", "tool_calls", "election_result", "summary",
        "user_interrupt", "interrupt_pending",
        "parliament_id", "started_at",
        "selected_expert_ids", "expert_selection_reasons",
        "debate_duties",
        "divergent_expert_ids",
        "agent_results", "agent_traces",
        "research_degraded", "research_error",
    }
    assert required_keys.issubset(state.keys()), f"missing: {required_keys - state.keys()}"


def test_make_initial_state_defaults():
    state = make_initial_state(topic="test")
    assert state["max_rounds"] == 3
    assert state["current_round"] == 0
    assert state["members"] == {}
    assert state["transcript"] == []
    assert state["election_result"] is None
    assert state["summary"] is None


def test_state_can_be_assigned_as_typeddict():
    state: ParliamentState = make_initial_state(topic="买房")
    state["current_round"] = 2
    state["members"] = {"suan": {"stance": "for"}}
    assert state["current_round"] == 2
    assert state["members"]["suan"]["stance"] == "for"


def test_make_initial_state_preserves_selected_experts():
    selected = ["mathematician", "economist", "psychologist"]
    state = make_initial_state(topic="买房", selected_expert_ids=selected)
    assert state["selected_expert_ids"] == selected
