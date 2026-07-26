import pytest

from app.agents.profiles import EXPERT_PROFILES, get_expert_profile


EXPECTED_IDS = {
    "mathematician",
    "economist",
    "psychologist",
    "historian",
    "artist",
    "politician",
}


def test_six_professional_profiles_are_complete():
    assert set(EXPERT_PROFILES) == EXPECTED_IDS
    for expert_id, profile in EXPERT_PROFILES.items():
        assert profile.expert_id == expert_id
        assert profile.display_name
        assert profile.analysis_methods
        assert profile.completion_criteria
        assert profile.communication_style
        assert profile.professional_boundaries
        assert set(profile.allowed_tools) <= {
            "web_search",
            "calculator",
            "memory_search",
        }


def test_professional_methods_match_identity():
    assert "calculator" in EXPERT_PROFILES["mathematician"].allowed_tools
    assert "calculator" in EXPERT_PROFILES["economist"].allowed_tools
    assert "calculator" not in EXPERT_PROFILES["psychologist"].allowed_tools
    assert "calculator" not in EXPERT_PROFILES["artist"].allowed_tools
    assert "价值澄清" in EXPERT_PROFILES["psychologist"].analysis_methods
    assert "问题重构" in EXPERT_PROFILES["artist"].analysis_methods
    assert any(
        "所有关系" in boundary
        for boundary in EXPERT_PROFILES["politician"].professional_boundaries
    )


def test_get_profile_rejects_unknown_id():
    with pytest.raises(ValueError, match="unknown expert"):
        get_expert_profile("fortune_teller")
