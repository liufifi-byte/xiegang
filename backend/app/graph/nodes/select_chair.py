"""Choose the chair deterministically from available expert output quality."""

from __future__ import annotations


def _chair_score(member: dict) -> float:
    confidence = float(member.get("confidence", 0.0) or 0.0)
    evidence = len(member.get("evidence", []) or [])
    degraded_penalty = 2.0 if member.get("degraded") else 0.0
    return confidence + min(evidence, 4) * 0.2 - degraded_penalty


def select_chair(state: dict) -> dict:
    selected = list(state.get("selected_expert_ids") or state.get("members", {}))
    members = state.get("members", {})
    available = [expert_id for expert_id in selected if expert_id in members]
    if not available:
        return {
            "election_result": {
                "candidates": [],
                "vote_tally": {},
                "recommenders": {},
                "winner": selected[0] if selected else "fan",
                "fallback_used": True,
                "fallback_reason": "没有可用专家结果，使用稳定兜底主席",
            }
        }

    scores = {
        expert_id: round(_chair_score(members[expert_id]), 3)
        for expert_id in available
    }
    winner = max(available, key=lambda expert_id: (scores[expert_id], -selected.index(expert_id)))
    return {
        "election_result": {
            "candidates": [],
            "vote_tally": scores,
            "recommenders": {},
            "winner": winner,
            "fallback_used": False,
            "fallback_reason": None,
            "selection_method": "evidence_quality",
        }
    }
