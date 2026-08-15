"""V3（spec §3/§7）：八人专家库选择测试——duties 已删除。"""
from app.graph.nodes.select_experts import DEFAULT_ROSTER, select_experts


def test_selector_returns_five_unique_known_experts():
    result = select_experts(
        {
            "topic": "要不要买房",
            "category": "real_estate",
            "briefing": {"key_variables": ["利率", "首付"]},
        }
    )
    selected = result["selected_expert_ids"]
    assert len(selected) == 5
    assert len(set(selected)) == 5
    assert "historian" in selected
    assert "legal_scholar" in selected     # 房产涉及合同/贷款条款
    assert "artist" not in selected        # 已删除的角色不得出现
    assert "politician" not in selected
    assert set(result["expert_selection_reasons"]) == set(selected)
    # V3：duties 删除——恒空 dict（state 形状兼容）
    assert result["debate_duties"] == {}


def test_offer_uses_stable_default_professional_roster():
    result = select_experts(
        {"topic": "要不要接 Offer", "category": "offer", "briefing": {}}
    )
    assert result["selected_expert_ids"] == list(DEFAULT_ROSTER)
    assert "strategist" in result["selected_expert_ids"]
    assert "contrarian" in result["selected_expert_ids"]


def test_general_relationship_topic_prefers_non_economic_lenses():
    result = select_experts(
        {
            "topic": "要不要结婚，双方家庭有冲突",
            "category": "general",
            "briefing": {"key_variables": ["关系", "家庭"]},
        }
    )
    selected = result["selected_expert_ids"]
    assert "psychologist" in selected
    assert "sociologist" in selected       # 家庭冲突 → 关系环境视角
    assert "contrarian" in selected     # 二选一措辞命中第三方案视角
    assert len(selected) == 5


def test_selector_is_deterministic():
    state = {
        "topic": "是否去国外读书",
        "category": "general",
        "briefing": {"key_variables": ["职业", "身份", "成本"]},
    }
    assert select_experts(state) == select_experts(state)


def test_selector_preserves_user_confirmed_roster():
    selected = ["contrarian", "psychologist", "historian"]
    result = select_experts(
        {
            "topic": "要不要换一种生活方式",
            "category": "general",
            "briefing": {},
            "selected_expert_ids": selected,
        }
    )
    assert result["selected_expert_ids"] == selected
    assert set(result["expert_selection_reasons"]) == set(selected)
    assert result["debate_duties"] == {}


def test_removed_roles_are_rejected():
    """已删除的角色 ID 不得再被选中（未知 profile 过滤）。"""
    result = select_experts(
        {
            "topic": "x",
            "category": "general",
            "briefing": {},
            "selected_expert_ids": ["artist", "politician", "economist"],
        }
    )
    # selection_state 过滤未知 ID
    assert "artist" not in result["selected_expert_ids"]
    assert "politician" not in result["selected_expert_ids"]
    assert "economist" in result["selected_expert_ids"]
