import json
from pathlib import Path

from app.agents.profiles import EXPERT_PROFILES
from app.agents.safety import sanitize_query


CASES_PATH = Path(__file__).with_name("agent_cases.json")
VALID_TOOLS = {"web_search", "calculator", "memory_search"}


def _load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def test_eval_set_has_thirty_unique_decision_cases_and_required_coverage():
    cases = _load_cases()
    assert len(cases) >= 30
    assert len({case["id"] for case in cases}) == len(cases)

    categories = {case["category"] for case in cases}
    assert {
        "offer",
        "job_change",
        "entrepreneurship",
        "real_estate",
        "investment",
        "education",
        "relationship",
        "family",
    } <= categories
    assert {case["info_level"] for case in cases} == {
        "complete",
        "partial",
        "severely_missing",
    }
    assert {case["search_expectation"] for case in cases} == {
        "required",
        "optional",
        "avoid",
    }
    assert sum(bool(case["sensitive_values"]) for case in cases) >= 6
    assert sum(case["adversarial"] for case in cases) >= 3


def test_eval_case_expectations_only_reference_real_experts_and_tools():
    for case in _load_cases():
        assert case["topic"].strip()
        assert case["background"].strip()
        assert set(case["expected_experts"]) <= set(EXPERT_PROFILES)
        assert set(case["expected_tools"]) <= VALID_TOOLS


def test_privacy_cases_prove_search_copy_can_be_safely_redacted():
    for case in _load_cases():
        sensitive_values = case["sensitive_values"]
        if not sensitive_values:
            continue
        raw_query = f'{case["topic"]} {case["background"]}'
        sanitized = sanitize_query(raw_query, sensitive_values)
        for secret in sensitive_values:
            assert secret not in sanitized
            assert secret not in case["expected_public_query"]


def test_avoid_search_cases_never_define_a_public_web_query():
    for case in _load_cases():
        if case["search_expectation"] == "avoid":
            assert case["expected_public_query"] == ""
