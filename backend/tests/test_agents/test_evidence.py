import pytest

from app.agents.evidence import EvidenceLedger
from app.agents.schemas import ToolObservation, ToolResultItem


def test_evidence_ledger_only_accepts_known_refs_and_deduplicates():
    ledger = EvidenceLedger("economist", max_records=3)
    observation = ToolObservation(
        tool_call_id="t1",
        tool_name="web_search",
        ok=True,
        results=[
            ToolResultItem(
                title="Salary report",
                url="https://example.com/report",
                content="行业薪资中位数为 40 万元",
                score=0.9,
            ),
            ToolResultItem(
                title="Salary report",
                url="https://example.com/report",
                content="行业薪资中位数为 40 万元",
                score=0.9,
            ),
        ],
    )
    added = ledger.add_from_observation(observation)
    assert len(added) == 1
    assert added[0].strength == "high"
    ledger.validate_refs([added[0].evidence_id])
    with pytest.raises(ValueError, match="unknown evidence"):
        ledger.validate_refs(["invented"])


def test_unlinked_result_cannot_be_high_strength():
    ledger = EvidenceLedger("psychologist")
    observation = ToolObservation(
        tool_call_id="t2",
        tool_name="memory_search",
        ok=True,
        results=[ToolResultItem(title="Memory", content="过去做过类似选择", score=1.0)],
    )
    record = ledger.add_from_observation(observation)[0]
    assert record.source_url is None
    assert record.strength == "medium"
    assert record.kind == "user_fact"


def test_search_snippet_without_url_is_not_admitted_as_web_evidence():
    ledger = EvidenceLedger("economist")
    observation = ToolObservation(
        tool_call_id="t3",
        tool_name="web_search",
        ok=True,
        results=[
            ToolResultItem(
                title="Unlinked snippet",
                content="某城市近期价格发生变化",
                score=0.9,
            )
        ],
    )
    assert ledger.add_from_observation(observation) == []


def test_calculation_records_inputs_and_formula():
    ledger = EvidenceLedger("mathematician")
    observation = ToolObservation(
        tool_call_id="t4",
        tool_name="calculator",
        ok=True,
        results=[
            ToolResultItem(
                title="安全计算结果",
                content="principal * rate = 96000",
                score=1.0,
                metadata={
                    "inputs": {"principal": 3000000, "rate": 0.032},
                    "formula": "principal * rate",
                },
            )
        ],
    )
    record = ledger.add_from_observation(observation)[0]
    assert record.kind == "calculation"
    assert record.strength == "high"
    assert record.calculation_formula == "principal * rate"
