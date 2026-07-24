from app.graph.nodes.select_chair import select_chair


def test_chair_prefers_evidence_quality():
    result = select_chair(
        {
            "selected_expert_ids": ["mathematician", "economist", "psychologist"],
            "members": {
                "mathematician": {"confidence": 0.8, "evidence": []},
                "economist": {"confidence": 0.7, "evidence": [{}, {}, {}]},
                "psychologist": {"confidence": 0.9, "evidence": [], "degraded": True},
            },
        }
    )["election_result"]
    assert result["winner"] == "economist"
    assert result["selection_method"] == "evidence_quality"
    assert result["fallback_used"] is False


def test_chair_has_stable_fallback_without_results():
    result = select_chair(
        {"selected_expert_ids": ["artist", "economist"], "members": {}}
    )["election_result"]
    assert result["winner"] == "artist"
    assert result["fallback_used"] is True
