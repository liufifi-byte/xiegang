import pytest

from app.graph.nodes.cross_examination import run_cross_examination


@pytest.mark.asyncio
async def test_cross_examination_scopes_debate_to_three(monkeypatch):
    from app.graph.nodes import cross_examination as module

    captured = {}

    async def fake_round(state):
        captured.update(state)
        return {"transcript": [{"type": "debate"}], "current_round": 99}

    monkeypatch.setattr(module, "run_debate_round", fake_round)
    result = await run_cross_examination(
        {
            "divergent_expert_ids": ["economist", "artist", "psychologist"],
            "selected_expert_ids": ["mathematician", "economist", "artist", "psychologist"],
        }
    )
    assert captured["selected_expert_ids"] == ["economist", "artist", "psychologist"]
    assert captured["max_rounds"] == 1
    assert result["current_round"] == 1
