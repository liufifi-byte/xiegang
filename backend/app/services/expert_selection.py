"""Stable expert recommendation shared by preview and council execution.

V3（spec §7）：八人专家库推荐 3-5 位。规则版先行（LLM 推荐为后续增强）。
duties 系统已删除——selection_state 不再写 debate_duties（写空 dict 保持
graph state 形状兼容）。
"""

from __future__ import annotations

from app.agents.profiles import EXPERT_PROFILES
from app.graph.nodes.select_experts import CATEGORY_ROSTERS, DEFAULT_ROSTER, _general_roster


CATEGORY_COVERAGE: dict[str, tuple[str, ...]] = {
    "real_estate": ("现金流", "机会成本", "居住动机", "市场周期", "权利边界"),
    "gold": ("收益风险", "机会成本", "行为偏差", "历史周期", "进入退路"),
    "investment": ("收益风险", "机会成本", "行为偏差", "历史基准", "行动顺序"),
    "stocks": ("估值变量", "机会成本", "情绪偏差", "行业周期", "权利边界"),
    "offer": ("条件比较", "机会成本", "职业动机", "行动顺序", "权利边界"),
    "job_change": ("决策变量", "机会成本", "职业动机", "第三方案", "权利边界"),
    "entrepreneurship": ("成功条件", "机会成本", "风险承受", "行动退路", "关系环境"),
    "buy_bag": ("预算边界", "机会成本", "消费动机", "关系环境", "被忽略的可能"),
}

CATEGORY_REASONS: dict[str, dict[str, str]] = {
    "real_estate": {
        "economist": "比较首付的机会成本、房租与替代收益。",
        "mathematician": "找出预算、月供、安全期与结论反转阈值。",
        "legal_scholar": "核对购房合同、贷款条款与产权边界。",
        "sociologist": "梳理家庭期待与共同承担的成本。",
        "historian": "用房地产周期和相似阶段校准短期判断。",
    },
    "offer": {
        "economist": "比较接受与拒绝这份工作的真实机会成本。",
        "psychologist": "澄清职业期待、身份认同和损失焦虑。",
        "strategist": "安排验证、谈判和入职/留任的行动顺序。",
        "legal_scholar": "核对竞业条款、股权文件与违约边界。",
        "contrarian": "检查是否存在分阶段加入等第三方案。",
    },
}


def recommend_experts(
    *,
    topic: str,
    topic_category: str | None,
    key_variables: list[str] | None = None,
) -> dict:
    """Return a deterministic 5-expert preview without starting a meeting."""
    category = topic_category or "general"
    roster = CATEGORY_ROSTERS.get(category)
    source = "category_rule"
    if roster is None:
        roster = _general_roster(topic, key_variables or [])
        source = "topic_rule"

    selected = [eid for eid in (roster or DEFAULT_ROSTER) if eid in EXPERT_PROFILES]
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
        if expert_id in EXPERT_PROFILES
    }
    return {
        "selected_expert_ids": [
            eid for eid in selected_expert_ids if eid in EXPERT_PROFILES
        ],
        "expert_selection_reasons": reasons,
        # V3: duties 删除——空 dict 保持 state 形状兼容
        "debate_duties": {},
    }
