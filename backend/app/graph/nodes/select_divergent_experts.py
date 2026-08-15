"""Select three useful, meaningfully different voices without another LLM call."""

from __future__ import annotations


STANCE_ORDER = ("support", "oppose", "neutral")


def _quality(member: dict) -> float:
    confidence = float(member.get("confidence", 0.0) or 0.0)
    evidence_count = len(member.get("evidence", []) or [])
    degraded_penalty = 1.0 if member.get("degraded") else 0.0
    error_penalty = 1.0 if "[RESEARCH ERROR]" in str(
        member.get("reasoning_summary", "")
    ) else 0.0
    return confidence + min(evidence_count, 3) * 0.15 - degraded_penalty - error_penalty


def select_divergent_experts(state: dict) -> dict:
    """Prefer stance coverage, then evidence quality, preserving roster order."""
    selected = list(state.get("selected_expert_ids") or state.get("members", {}))
    members = state.get("members", {})
    roster_index = {expert_id: index for index, expert_id in enumerate(selected)}

    ranked = sorted(
        [expert_id for expert_id in selected if expert_id in members],
        key=lambda expert_id: (
            -_quality(members[expert_id]),
            roster_index.get(expert_id, 999),
        ),
    )

    chosen: list[str] = []
    for stance in STANCE_ORDER:
        candidate = next(
            (
                expert_id
                for expert_id in ranked
                if expert_id not in chosen
                and members[expert_id].get("stance") == stance
            ),
            None,
        )
        if candidate:
            chosen.append(candidate)

    for expert_id in ranked:
        if len(chosen) >= 3:
            break
        if expert_id not in chosen:
            chosen.append(expert_id)

    return {"divergent_expert_ids": chosen[:3]}
