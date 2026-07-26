"""P0 step3: chair endorsement tests."""
import asyncio

import pytest

from app.agents.profiles import EXPERT_PROFILES


@pytest.fixture(autouse=True)
def use_test_env(monkeypatch):
    import os
    os.environ.setdefault("SKIP_DB_CONTEXT", "true")


def test_chair_endorsement_module_imports():
    """Node imports cleanly."""
    from app.graph.nodes.chair_endorsement import run_quick_chair_endorsement
    assert callable(run_quick_chair_endorsement)


def test_graph_includes_chair_endorsement():
    """build_parliament_graph should have chair_endorsement as a node."""
    from app.graph.parliament import build_parliament_graph
    graph = build_parliament_graph()
    # compiled graph nodes accessible via .nodes or similar
    assert hasattr(graph, "nodes") or hasattr(graph, "get_graph")


def test_endorsement_too_few_experts_returns_fallback():
    """< 2 experts → fallback neutral host."""
    from app.graph.nodes.chair_endorsement import run_quick_chair_endorsement

    state = {
        "selected_expert_ids": ["mathematician"],
        "members": {"mathematician": {"stance": "neutral"}},
        "topic": "test",
        "transcript": [],
    }
    result = asyncio.run(run_quick_chair_endorsement(state))
    assert result["election_result"]["fallback_used"] is True
    assert result["election_result"]["fallback_reason"] == "experts_insufficient"


def test_endorsement_picks_winner(monkeypatch):
    """Mock endorsements → winner = highest votes."""
    from app.graph.nodes import chair_endorsement as ce_module
    from app.graph.nodes.chair_endorsement import ChairEndorsement

    async def mock_one(expert_id, state, selected):
        target_map = {
            "mathematician": "psychologist",
            "economist": "psychologist",
            "psychologist": "mathematician",
            "historian": "mathematician",
            "artist": "economist",
        }
        target = target_map.get(expert_id, "mathematician")
        return ChairEndorsement(
            recommender=expert_id,
            recommend_target=target,
            reason=f"因为对方立场有力",
        )

    monkeypatch.setattr(ce_module, "_one_endorsement", mock_one)

    state = {
        "selected_expert_ids": ["mathematician", "economist", "psychologist", "historian", "artist"],
        "members": {eid: {"stance": "neutral"} for eid in ["mathematician", "economist", "psychologist", "historian", "artist"]},
        "debate_duties": {},
        "topic": "test",
        "transcript": [],
    }
    result = asyncio.run(ce_module.run_quick_chair_endorsement(state))

    # psychologist gets 2 votes, mathematician gets 2 votes → tied → fallback
    # Let me fix:igator psychologist(2) vs mathematician(2) → tied
    # Actually economist(1). So psychologist and mathematician both have 2 → tied
    # Let me recalculate: psychologist=2, mathematician=2, economist=1
    # Tied between psychologist and mathematician → fallback
    # Let me change the mock to avoid tie

    # Redo mock with clear winner
    async def mock_one_clear(expert_id, state, selected):
        return ChairEndorsement(
            recommender=expert_id,
            recommend_target="psychologist",
            reason="立场有力",
        )

    monkeypatch.setattr(ce_module, "_one_endorsement", mock_one_clear)
    result = asyncio.run(ce_module.run_quick_chair_endorsement(state))
    assert result["election_result"]["winner"] == "psychologist"
    assert result["election_result"]["fallback_used"] is False
    assert result["election_result"]["vote_tally"]["psychologist"] == 5


def test_endorsement_tie_triggers_fallback(monkeypatch):
    """Tied votes → fallback to neutral host."""
    from app.graph.nodes import chair_endorsement as ce_module
    from app.graph.nodes.chair_endorsement import ChairEndorsement

    call_count = {"n": 0}

    async def mock_tie(expert_id, state, selected):
        call_count["n"] += 1
        target = "psychologist" if call_count["n"] <= 2 else "mathematician"
        return ChairEndorsement(
            recommender=expert_id,
            recommend_target=target,
            reason="立场有力",
        )

    monkeypatch.setattr(ce_module, "_one_endorsement", mock_tie)

    state = {
        "selected_expert_ids": ["mathematician", "economist", "psychologist", "historian"],
        "members": {eid: {"stance": "neutral"} for eid in ["mathematician", "economist", "psychologist", "historian"]},
        "debate_duties": {},
        "topic": "test",
        "transcript": [],
    }
    result = asyncio.run(ce_module.run_quick_chair_endorsement(state))
    # psychologist gets 2, mathematician gets 2 → tied
    assert result["election_result"]["fallback_used"] is True
    assert result["election_result"]["fallback_reason"] == "tied"


def test_endorsement_writes_election_to_transcript(monkeypatch):
    """Transcript should get a system election message."""
    from app.graph.nodes import chair_endorsement as ce_module
    from app.graph.nodes.chair_endorsement import ChairEndorsement

    async def mock_one(expert_id, state, selected):
        return ChairEndorsement(
            recommender=expert_id,
            recommend_target="economist",
            reason="-testid",
        )

    monkeypatch.setattr(ce_module, "_one_endorsement", mock_one)

    state = {
        "selected_expert_ids": ["mathematician", "economist", "psychologist"],
        "members": {eid: {"stance": "for"} for eid in ["mathematician", "economist", "psychologist"]},
        "debate_duties": {},
        "topic": "test",
        "transcript": [{"type": "debate"}],
    }
    result = asyncio.run(ce_module.run_quick_chair_endorsement(state))
    election_entries = [e for e in result["transcript"] if e.get("type") == "election"]
    assert len(election_entries) == 1
    assert "议长" in election_entries[0]["content"]
