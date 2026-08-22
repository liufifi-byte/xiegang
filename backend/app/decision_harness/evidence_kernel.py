"""Evidence Kernel（spec 2026-08-22 §12，Phase 4）。

共享证据内核：Claim-Evidence 映射 + 来源权威性/时效策略 + 状态机。

铁律（§12.1/§12.2/§12.4）：
- web_source 证据必须有 source_url（schema 层已强制）；
- unverified/rejected/stale 不得支撑核心结论；
- 自媒体/社区/营销页只能作线索，不能成为唯一依据；
- 搜索摘要只存 excerpt，绝不整段进入公开回复（§15.2）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from app.decision_harness.schemas import (
    EvidenceKind,
    EvidenceRecordV4,
    EvidenceStatus,
)

# §12.2 来源路由：权威层级（高→低）
AUTHORITY_DOMAIN_RULES: list[tuple[tuple[str, ...], str]] = [
    (
        ("gov.cn", "gov", "gov.uk", "court", "court.gov", "mps.gov", "nist.gov",
         "who.int", "imf.org", "bis.org", "stats.gov.cn", "sec.gov", "hkex.com.hk",
         "csrc.gov.cn", "pboc.gov.cn", "samr.gov.cn", "moh.gov.cn", "nhc.gov.cn"),
        "official",
    ),
    (
        ("edu", "ac.cn", "org", "nature.com", "science.org", "nejm.org",
         "thelancet.com", "apa.org", "ama-assn.org", "scholar", "jstor.org",
         "archive.org", "cas.cn", "pku.edu.cn", "tsinghua.edu.cn"),
        "authoritative",
    ),
    (
        ("law", "contract", "disclosure", "annualreports", "hkexnews.hk",
         "cninfo.com.cn", "sse.com.cn", "szse.cn"),
        "professional",
    ),
    (
        ("reuters.com", "bloomberg.com", "ft.com", "wsj.com", "caixin.com",
         "xinhuanet.com", "people.com.cn", "thepaper.cn", "ftchinese.com"),
        "media",
    ),
    (
        ("zhihu.com", "tieba", "reddit.com", "quora.com", "weibo.com",
         "xiaohongshu", "douyin", "medium.com", "blog", "bbs", "forum"),
        "community",
    ),
]

_LOW_AUTHORITY = {"media", "community", "unknown"}

# §12.2 时效窗口（按领域）：超过即 stale 候选（金融快、历史慢）
FRESHNESS_WINDOWS_DAYS = {
    "finance": 365,
    "legal": 1825,
    "medical": 730,
    "company": 365,
    "history": 3650,
    "general": 730,
}

_DEFAULT_WINDOW_DAYS = 730


def classify_authority(source_url: str, source_domain: str = "") -> str:
    """按域名映射权威层级（§12.2 路由表的程序化近似）。"""
    domain = (source_domain or "").lower()
    if not domain and source_url:
        try:
            domain = urlparse(source_url).netloc.lower()
        except Exception:
            domain = ""
    for markers, level in AUTHORITY_DOMAIN_RULES:
        for marker in markers:
            if marker in domain:
                return level
    return "unknown"


def _reliability_for(level: str) -> float:
    return {
        "official": 0.95,
        "authoritative": 0.9,
        "professional": 0.8,
        "media": 0.6,
        "community": 0.3,
        "unknown": 0.4,
    }.get(level, 0.4)


def is_stale(record: EvidenceRecordV4, domain: str = "general",
             now: datetime | None = None) -> bool:
    """时效检查：published_at（缺省 retrieved_at）超过领域窗口即过期。"""
    anchor = record.published_at or record.retrieved_at
    if anchor is None:
        return False
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    window = timedelta(days=FRESHNESS_WINDOWS_DAYS.get(domain, _DEFAULT_WINDOW_DAYS))
    return (now - anchor) > window


@dataclass
class Claim:
    """待核实断言（Claim Decomposition 的输出）。"""

    claim_id: str
    text: str
    domain: str = "general"  # finance/legal/medical/company/history/general
    importance: float = 0.5


@dataclass
class ClaimVerdict:
    claim: Claim
    status: EvidenceStatus
    evidence_ids: list[str] = field(default_factory=list)
    note: str = ""


class EvidenceKernel:
    """共享证据内核：写入、评分、交叉验证、claim 判定。

    上层（Research Lead 写、Auditor 改状态、专家只读）共用一份；
    持久化经 repository.upsert_evidence（本类不直接碰 SQL）。
    """

    def __init__(self, *, run_id: str):
        self.run_id = run_id
        self._claims: dict[str, Claim] = {}
        self._records: dict[str, EvidenceRecordV4] = {}
        # claim_id -> [evidence_id]
        self._claim_evidence: dict[str, list[str]] = {}

    # ---- Claim ----

    def add_claim(self, claim: Claim) -> None:
        self._claims[claim.claim_id] = claim
        self._claim_evidence.setdefault(claim.claim_id, [])

    @property
    def claims(self) -> list[Claim]:
        return list(self._claims.values())

    # ---- Evidence ----

    def register(self, record: EvidenceRecordV4,
                 claim_id: str | None = None) -> EvidenceStatus:
        """写入证据并做即时评分（权威+时效）。返回落库后的状态。"""
        level = classify_authority(record.source_url or "", record.source_domain)
        updates: dict = {
            "source_domain": record.source_domain or _domain_of(record.source_url),
        }
        if record.kind == EvidenceKind.WEB_SOURCE:
            # 权威性只对 web 来源有意义：按域名定层级与可靠性下限；
            # inference/calculation/user_fact 保留声明值，交由审计裁决
            updates["authority_level"] = level
            updates["reliability"] = max(record.reliability, _reliability_for(level))
        record = record.model_copy(update=updates)
        if record.kind == EvidenceKind.WEB_SOURCE:
            domain = self._claims[claim_id].domain if claim_id in self._claims else "general"
            if is_stale(record, domain=domain):
                record = record.model_copy(update={"status": EvidenceStatus.STALE})
            elif record.status == EvidenceStatus.UNVERIFIED and level not in ("community", "unknown"):
                # media 可被验证（可作佐证），但 verdict 的"强源"判定
                # 仍排除 media——媒体不能独撑结论（§12.2）
                record = record.model_copy(update={"status": EvidenceStatus.VERIFIED})
        self._records[record.id] = record
        if claim_id:
            self._claim_evidence.setdefault(claim_id, [])
            if record.id not in self._claim_evidence[claim_id]:
                self._claim_evidence[claim_id].append(record.id)
        return record.status

    def mark_rejected(self, evidence_id: str, reason: str = "") -> None:
        record = self._records.get(evidence_id)
        if record:
            self._records[evidence_id] = record.model_copy(update={
                "status": EvidenceStatus.REJECTED,
                "metadata": {**record.metadata, "reject_reason": reason},
            })

    def mark_disputed(self, evidence_id: str, reason: str = "") -> None:
        record = self._records.get(evidence_id)
        if record:
            self._records[evidence_id] = record.model_copy(update={
                "status": EvidenceStatus.DISPUTED,
                "metadata": {**record.metadata, "dispute_reason": reason},
            })

    def get(self, evidence_id: str) -> EvidenceRecordV4 | None:
        return self._records.get(evidence_id)

    @property
    def records(self) -> list[EvidenceRecordV4]:
        return list(self._records.values())

    def evidence_for_claim(self, claim_id: str) -> list[EvidenceRecordV4]:
        return [
            self._records[eid]
            for eid in self._claim_evidence.get(claim_id, [])
            if eid in self._records
        ]

    # ---- 判定 ----

    def verdict(self, claim_id: str) -> ClaimVerdict:
        """§12.4：claim → 可用证据判定。

        corroborated：≥2 个独立域名的可用（verified/corroborated）证据；
        verified：≥1 个非低权威可用证据；
        unverified：只有低权威（media/community/unknown）线索——
        低质量来源不能成为唯一依据；
        disputed：可用证据中存在对立支持/反对。
        """
        claim = self._claims.get(claim_id)
        if claim is None:
            return ClaimVerdict(
                Claim(claim_id=claim_id, text=""), EvidenceStatus.UNVERIFIED,
                note="unknown_claim",
            )
        usable = [
            r for r in self.evidence_for_claim(claim_id)
            if r.status in (EvidenceStatus.VERIFIED, EvidenceStatus.CORROBORATED)
        ]
        strong = [r for r in usable if r.authority_level not in _LOW_AUTHORITY]
        opposed = [r for r in self.evidence_for_claim(claim_id)
                   if r.status != EvidenceStatus.REJECTED and r.contradicts_claim]
        ids = [r.id for r in usable]

        if opposed and usable:
            return ClaimVerdict(claim, EvidenceStatus.DISPUTED, ids,
                                "contradicting_evidence")
        # corroborated：独立双域 + 至少一个非低权威（媒体可佐证官方，
        # 但纯低权威组合不能支撑结论，§12.2）
        if strong and len({_domain_of(r.source_url) for r in usable}) >= 2:
            return ClaimVerdict(claim, EvidenceStatus.CORROBORATED, ids)
        if strong:
            return ClaimVerdict(claim, EvidenceStatus.VERIFIED, ids)
        low = [r for r in self.evidence_for_claim(claim_id)
               if r.status not in (EvidenceStatus.REJECTED, EvidenceStatus.STALE)]
        note = "low_authority_only" if low else "no_usable_evidence"
        return ClaimVerdict(claim, EvidenceStatus.UNVERIFIED,
                            [r.id for r in low], note)

    def usable_summary(self) -> list[dict]:
        """给 Context Compiler 的证据摘要（白名单字段，§15.2：不带 excerpt）。"""
        return [
            {
                "id": r.id,
                "claim_id": r.claim_id,
                "kind": r.kind.value if hasattr(r.kind, "value") else str(r.kind),
                "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                "source_title": r.source_title[:120],
                "source_url": r.source_url,
                "authority_level": r.authority_level,
                "reliability": r.reliability,
            }
            for r in self._records.values()
            if r.status in (EvidenceStatus.VERIFIED, EvidenceStatus.CORROBORATED)
        ]


def _domain_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""
