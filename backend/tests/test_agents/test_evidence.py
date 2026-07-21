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
    assert record.strength == "low"
