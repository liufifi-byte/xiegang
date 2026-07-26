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
    assert "artist" not in selected
    assert set(result["expert_selection_reasons"]) == set(selected)
    assert set(result["debate_duties"]) == set(selected)
    assert set(result["debate_duties"].values()) == {
        "evidence_audit",
        "downside_risk",
        "opportunity_scan",
        "long_term_path",
        "contrarian_challenge",
    }
    assert result["debate_duties"] == {
        "mathematician": "evidence_audit",
        "historian": "downside_risk",
        "economist": "opportunity_scan",
        "psychologist": "long_term_path",
        "politician": "contrarian_challenge",
    }


def test_offer_uses_stable_default_professional_roster():
    result = select_experts(
        {"topic": "要不要接 Offer", "category": "offer", "briefing": {}}
    )
    assert result["selected_expert_ids"] == list(DEFAULT_ROSTER)


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
    assert "artist" in selected
    assert "politician" in selected
    assert len(selected) == 5


def test_selector_is_deterministic():
    state = {
        "topic": "是否去国外读书",
        "category": "general",
        "briefing": {"key_variables": ["职业", "身份", "成本"]},
    }
    assert select_experts(state) == select_experts(state)


def test_selector_preserves_user_confirmed_roster():
    selected = ["artist", "psychologist", "historian"]
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
    assert set(result["debate_duties"]) == set(selected)
