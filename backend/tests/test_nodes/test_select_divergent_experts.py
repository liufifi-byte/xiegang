from app.graph.nodes.select_divergent_experts import select_divergent_experts


def test_selects_three_stance_diverse_experts():
    state = {
        "selected_expert_ids": ["mathematician", "economist", "psychologist", "artist"],
        "members": {
            "mathematician": {"stance": "for", "confidence": 0.7, "evidence": [{}]},
            "economist": {"stance": "against", "confidence": 0.8, "evidence": [{}, {}]},
            "psychologist": {"stance": "neutral", "confidence": 0.6, "evidence": [{}]},
            "artist": {"stance": "for", "confidence": 0.9, "evidence": []},
        },
    }
    result = select_divergent_experts(state)
    chosen = result["divergent_expert_ids"]
    assert len(chosen) == 3
    assert {state["members"][expert_id]["stance"] for expert_id in chosen} == {
        "for",
        "against",
        "neutral",
    }


def test_degraded_expert_loses_quality_tie():
    state = {
        "selected_expert_ids": ["mathematician", "economist", "artist"],
        "members": {
            "mathematician": {"stance": "for", "confidence": 0.8, "evidence": []},
            "economist": {"stance": "for", "confidence": 0.9, "evidence": [], "degraded": True},
            "artist": {"stance": "against", "confidence": 0.7, "evidence": []},
        },
    }
    chosen = select_divergent_experts(state)["divergent_expert_ids"]
    assert chosen.index("mathematician") < chosen.index("economist")
