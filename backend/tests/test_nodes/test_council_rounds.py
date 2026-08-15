from app.graph.nodes.council_rounds import (
    assess_third_round,
    extract_core_disputes,
    route_after_round_two,
)


def _state(**overrides):
    state = {
        "selected_expert_ids": [
            "mathematician",
            "economist",
            "psychologist",
            "historian",
            "politician",
        ],
        "members": {
            "mathematician": {"stance": "for"},
            "economist": {"stance": "against"},
            "psychologist": {"stance": "neutral"},
            "historian": {"stance": "against"},
            "politician": {"stance": "for"},
        },
        "briefing": {"key_variables": ["现金流", "持有期限"]},
        "debate_duties": {"politician": "contrarian_challenge"},
        "transcript": [],
        "round_decisions": [],
        "current_round": 2,
        "tension_score": 0.3,
    }
    state.update(overrides)
    return state


def test_extracts_at_most_three_real_disputes_with_two_to_four_participants():
    result = extract_core_disputes(_state())
    disputes = result["core_disputes"]
    assert 1 <= len(disputes) <= 3
    assert all(2 <= len(item["participants"]) <= 4 for item in disputes)
    assert all(item["pressure_test"] is False for item in disputes)


def test_unanimous_opening_creates_pressure_test():
    state = _state(
        members={
            expert_id: {"stance": "for"}
            for expert_id in _state()["selected_expert_ids"]
        }
    )
    disputes = extract_core_disputes(state)["core_disputes"]
    assert disputes[0]["pressure_test"] is True
    # V3: roster 不含 contrarian 时退回首位参会者（逆向缺席容忍）
    assert len(disputes[0]["participants"]) >= 1


def test_two_rounds_are_enough_when_single_low_tension_dispute_is_resolved():
    state = _state(
        core_disputes=[
            {
                "dispute_id": "d1",
                "question": "现金流是否可承受？",
                "participants": ["mathematician", "economist"],
            }
        ],
        transcript=[
            {
                "round": 2,
                "type": "debate",
                "speaker": "economist",
                "content": "双方同意以现金流阈值作为条件判断。",
            }
        ],
    )
    result = assess_third_round(state)
    assert result["needs_third_round"] is False
    routed = {**state, **result}
    assert route_after_round_two(routed) == "summary"


def test_multiple_open_disputes_trigger_third_round_but_never_after_round_three():
    state = _state(
        core_disputes=[
            {"dispute_id": "d1", "question": "A", "participants": []},
            {"dispute_id": "d2", "question": "B", "participants": []},
        ]
    )
    result = assess_third_round(state)
    assert result["needs_third_round"] is True
    assert route_after_round_two({**state, **result}) == "convergence"
    assert (
        route_after_round_two({**state, **result, "current_round": 3})
        == "summary"
    )
