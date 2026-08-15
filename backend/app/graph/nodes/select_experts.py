"""Deterministically select 3-5 relevant professional experts for a meeting.

V3（spec §3/§7）：八人专家库。系统推荐 3-5 位（用户可在前端修改最终阵容）。
duties 系统已删除——相关性取代固定职责（spec §17.4）。
"""

from __future__ import annotations

from app.agents.profiles import EXPERT_PROFILES


# spec §7 决策链顺序： economist → psychologist → historian → mathematician
# → strategist → legal_scholar → sociologist → contrarian
_CHAIN_ORDER = (
    "economist", "psychologist", "historian", "mathematician",
    "strategist", "legal_scholar", "sociologist", "contrarian",
)

DEFAULT_ROSTER = (
    "economist",
    "psychologist",
    "strategist",
    "legal_scholar",
    "contrarian",
)

# spec §7 推荐规则的场景版（规则先行；LLM 推荐为后续增强）：
# - 涉及合同/劳动/财产/责任 → legal_scholar 优先
# - 涉及家庭共同承担/组织约束 → sociologist 优先
# - 二选一困境 → contrarian 优先
# - 重大个人价值冲突 → psychologist 优先
# - 高度量化 → mathematician 优先
CATEGORY_ROSTERS: dict[str, tuple[str, ...]] = {
    "real_estate": (
        "economist", "mathematician", "legal_scholar", "sociologist", "historian",
    ),
    "gold": (
        "economist", "mathematician", "historian", "strategist", "contrarian",
    ),
    "investment": (
        "economist", "mathematician", "strategist", "historian", "contrarian",
    ),
    "stocks": (
        "economist", "mathematician", "strategist", "historian", "contrarian",
    ),
    "offer": DEFAULT_ROSTER,
    "job_change": DEFAULT_ROSTER,
    "entrepreneurship": (
        "economist", "strategist", "legal_scholar", "psychologist", "contrarian",
    ),
    "buy_bag": (
        "psychologist", "economist", "sociologist", "contrarian", "mathematician",
    ),
}

# 话题级加降权（关键词规则）
_KEYWORD_BOOSTS = {
    "legal_scholar": ("合同", "协议", "竞业", "违约", "诉讼", "产权", "版权", "条款", "法律责任"),
    "sociologist": ("家庭", "父母", "配偶", "婚姻", "孩子", "合伙人", "团队", "公司规矩", "亲戚"),
    "contrarian": ("还是", "要不要", "二选一", "纠结", "摇摆", "大家都", "别人都"),
    "psychologist": ("焦虑", "迷茫", "痛苦", "意义", "想要什么", "逃避", "讨厌"),
    "mathematician": ("多少", "比例", "数字", "计算", "概率", "预算", "收益率"),
}
_KEYWORD_EXCLUSIONS = {
    "historian": ("今天", "立刻", "马上", "短期", "这周"),
    "legal_scholar": ("心情", "感受", "关系", "相处"),
}


def _general_roster(topic: str, variables: list[str]) -> tuple[str, ...]:
    text = f"{topic} {' '.join(variables)}"
    scores = {expert_id: 10 for expert_id in _CHAIN_ORDER}
    for expert_id, keywords in _KEYWORD_BOOSTS.items():
        if any(k in text for k in keywords):
            scores[expert_id] += 4
    for expert_id, keywords in _KEYWORD_EXCLUSIONS.items():
        if any(k in text for k in keywords):
            scores[expert_id] -= 4
    ordered = sorted(
        _CHAIN_ORDER,
        key=lambda expert_id: (-scores[expert_id], _CHAIN_ORDER.index(expert_id)),
    )
    return tuple(ordered[:5])


def select_experts(state: dict) -> dict:
    category = state.get("category") or state.get("topic_category") or "general"
    confirmed = state.get("selected_expert_ids") or []
    if confirmed:
        from app.services.expert_selection import selection_state

        return selection_state(list(confirmed))
    variables = state.get("briefing", {}).get("key_variables", [])
    roster = CATEGORY_ROSTERS.get(category)
    if roster is None:
        roster = _general_roster(state.get("topic", ""), variables)
    selected = list(roster)
    reasons = {
        expert_id: EXPERT_PROFILES[expert_id].professional_identity
        for expert_id in selected
        if expert_id in EXPERT_PROFILES
    }
    return {
        "selected_expert_ids": selected,
        "expert_selection_reasons": reasons,
        # V3: debate_duties 已删除；写空 dict 保持旧 checkpoint state 形状兼容
        "debate_duties": {},
    }
