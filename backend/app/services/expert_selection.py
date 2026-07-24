"""Stable expert recommendation shared by preview and council execution."""

from __future__ import annotations

from app.agents.profiles import EXPERT_PROFILES, assign_parliament_duties
from app.graph.nodes.select_experts import CATEGORY_ROSTERS, DEFAULT_ROSTER, _general_roster


CATEGORY_COVERAGE: dict[str, tuple[str, ...]] = {
    "real_estate": ("现金流", "机会成本", "居住需求", "市场周期", "利益相关者"),
    "gold": ("收益风险", "机会成本", "风险偏好", "历史周期", "政策环境"),
    "investment": ("收益风险", "机会成本", "行为偏差", "历史基准", "利益结构"),
    "stocks": ("估值变量", "机会成本", "情绪偏差", "行业周期", "治理结构"),
    "offer": ("条件比较", "机会成本", "职业动机", "第三方案", "谈判空间"),
    "job_change": ("决策变量", "机会成本", "职业动机", "第三方案", "谈判空间"),
    "entrepreneurship": ("成功条件", "机会成本", "风险承受", "替代路径", "利益结构"),
    "buy_bag": ("预算边界", "机会成本", "消费动机", "长期使用", "审美价值"),
}

CATEGORY_REASONS: dict[str, dict[str, str]] = {
    "real_estate": {
        "mathematician": "比较预算、月供、持有期限与结论反转点。",
        "economist": "分析首付的机会成本、房租与替代收益。",
        "psychologist": "区分真实居住需要与害怕错过的压力。",
        "historian": "用房地产周期和相似阶段校准短期判断。",
        "politician": "梳理家庭、开发商、银行与政策各方影响。",
    },
    "offer": {
        "mathematician": "把薪酬、股权、成长和风险变成可比较条件。",
        "economist": "比较接受与拒绝这份工作的真实机会成本。",
        "psychologist": "澄清职业期待、身份认同和损失焦虑。",
        "artist": "寻找谈判、试用或组合式合作等第三方案。",
        "politician": "判断你的筹码、对方需求与可谈判空间。",
    },
}


def recommend_experts(
    *,
    topic: str,
    topic_category: str | None,
    key_variables: list[str] | None = None,
) -> dict:
    """Return a deterministic five-expert preview without starting a meeting."""
    category = topic_category or "general"
    roster = CATEGORY_ROSTERS.get(category)
    source = "category_rule"
    if roster is None:
        roster = _general_roster(topic, key_variables or [])
        source = "topic_rule"

    selected = list(roster or DEFAULT_ROSTER)
    category_reasons = CATEGORY_REASONS.get(category, {})
    reasons = {
        expert_id: category_reasons.get(
            expert_id,
            EXPERT_PROFILES[expert_id].professional_identity,
        )
        for expert_id in selected
    }
    coverage = list(
        CATEGORY_COVERAGE.get(
            category,
            tuple(
                EXPERT_PROFILES[expert_id].analysis_methods[0]
                for expert_id in selected
            ),
        )
    )
    return {
        "recommended_expert_ids": selected,
        "coverage": coverage,
        "reasons": reasons,
        "selection_source": source,
    }


def selection_state(selected_expert_ids: list[str]) -> dict:
    """Build graph fields for a roster already confirmed by the user."""
    reasons = {
        expert_id: EXPERT_PROFILES[expert_id].professional_identity
        for expert_id in selected_expert_ids
    }
    return {
        "selected_expert_ids": list(selected_expert_ids),
        "expert_selection_reasons": reasons,
        "debate_duties": assign_parliament_duties(selected_expert_ids),
    }
