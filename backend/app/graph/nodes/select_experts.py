"""Deterministically select five relevant professional experts for a meeting."""

from __future__ import annotations

from app.agents.profiles import EXPERT_PROFILES, assign_parliament_duties


DEFAULT_ROSTER = (
    "mathematician",
    "economist",
    "psychologist",
    "artist",
    "politician",
)

CATEGORY_ROSTERS: dict[str, tuple[str, ...]] = {
    "real_estate": (
        "mathematician",
        "economist",
        "psychologist",
        "historian",
        "politician",
    ),
    "gold": (
        "mathematician",
        "economist",
        "psychologist",
        "historian",
        "politician",
    ),
    "investment": (
        "mathematician",
        "economist",
        "psychologist",
        "historian",
        "politician",
    ),
    "stocks": (
        "mathematician",
        "economist",
        "psychologist",
        "historian",
        "politician",
    ),
    "offer": DEFAULT_ROSTER,
    "job_change": DEFAULT_ROSTER,
    "entrepreneurship": DEFAULT_ROSTER,
    "buy_bag": (
        "mathematician",
        "economist",
        "psychologist",
        "historian",
        "artist",
    ),
}

_KEYWORD_EXCLUSIONS = {
    "politician": ("买包", "审美", "创作", "艺术", "个人消费"),
    "historian": ("全新", "从零", "创意", "生活方式", "身份"),
    "artist": ("利率", "贷款", "估值", "政策", "资产配置"),
    "mathematician": ("关系", "结婚", "分手", "家庭冲突"),
    "economist": ("关系", "结婚", "情感", "亲密"),
    "psychologist": ("纯技术", "算法选型"),
}


def _general_roster(topic: str, variables: list[str]) -> tuple[str, ...]:
    text = f"{topic} {' '.join(variables)}"
    scores = {expert_id: 10 for expert_id in EXPERT_PROFILES}
    for expert_id, keywords in _KEYWORD_EXCLUSIONS.items():
        if any(keyword in text for keyword in keywords):
            scores[expert_id] -= 4
    ordered = sorted(
        EXPERT_PROFILES,
        key=lambda expert_id: (-scores[expert_id], list(EXPERT_PROFILES).index(expert_id)),
    )
    return tuple(ordered[:5])


def select_experts(state: dict) -> dict:
    category = state.get("category") or state.get("topic_category") or "general"
    variables = state.get("briefing", {}).get("key_variables", [])
    roster = CATEGORY_ROSTERS.get(category)
    if roster is None:
        roster = _general_roster(state.get("topic", ""), variables)
    selected = list(roster)
    reasons = {
        expert_id: EXPERT_PROFILES[expert_id].professional_identity
        for expert_id in selected
    }
    return {
        "selected_expert_ids": selected,
        "expert_selection_reasons": reasons,
        "debate_duties": assign_parliament_duties(selected),
    }
