"""One bounded cross-examination round for the three most divergent experts."""

from __future__ import annotations

from app.graph.nodes.round_debate import run_debate_round


async def run_cross_examination(state: dict) -> dict:
    selected = list(state.get("divergent_expert_ids", []))
    if not selected:
        return {"current_round": 1}

    scoped_state = dict(state)
    scoped_state["selected_expert_ids"] = selected
    scoped_state["max_rounds"] = 1
    result = await run_debate_round(scoped_state)
    result["current_round"] = 1
    return result
