"""Evidence ledger: the only source of externally cited facts in final output."""

from __future__ import annotations

import hashlib
import time
from urllib.parse import urlparse

from app.agents.schemas import EvidenceRecord, ToolObservation


class EvidenceLedger:
    def __init__(self, agent_id: str, max_records: int = 3):
        self.agent_id = agent_id
        self.max_records = max_records
        self._records: dict[str, EvidenceRecord] = {}

    @property
    def records(self) -> list[EvidenceRecord]:
        return list(self._records.values())

    def add_from_observation(self, observation: ToolObservation) -> list[EvidenceRecord]:
        added: list[EvidenceRecord] = []
        if not observation.ok:
            return added
        for item in observation.results:
            if len(self._records) >= self.max_records:
                break
            seed = f"{item.url or ''}|{item.content[:240]}"
            evidence_id = "ev_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
            if evidence_id in self._records:
                continue
            domain = urlparse(item.url).netloc if item.url else ""
            strength = "low"
            if item.url and (item.score or 0) >= 0.75:
                strength = "high"
            elif item.url:
                strength = "medium"
            record = EvidenceRecord(
                evidence_id=evidence_id,
                agent_id=self.agent_id,
                tool_call_id=observation.tool_call_id,
                claim_summary=item.content[:180],
                source_title=item.title,
                source_url=item.url,
                source_domain=domain,
                source_excerpt=item.content[:500],
                retrieved_at=time.time(),
                relevance=max(0.0, min(1.0, item.score or 0.5)),
                reliability=0.65 if item.url else 0.3,
                strength=strength,
            )
            self._records[evidence_id] = record
            added.append(record)
        return added

    def contains(self, evidence_id: str) -> bool:
        return evidence_id in self._records

    def validate_refs(self, refs: list[str]) -> None:
        unknown = [ref for ref in refs if ref not in self._records]
        if unknown:
            raise ValueError(f"unknown evidence refs: {', '.join(unknown)}")

    def to_prompt(self) -> str:
        if not self._records:
            return "（尚无外部证据）"
        return "\n".join(
            f"[{record.evidence_id}] {record.claim_summary} — "
            f"{record.source_title} ({record.source_domain or '无公开链接'})"
            for record in self._records.values()
        )
