"""Research Lead（spec 2026-08-22 §5.2 / §12.3，Phase 4）。

Claim 驱动的搜索循环：
    Claim Decomposition（上层给定 claims）
    → Source Plan（按领域生成查询）
    → Candidate Search（SearchProvider）
    → Original Source Fetch（尽力；失败降级为搜索级证据）
    → Authority/Freshness Check（EvidenceKernel.register 内完成）
    → Corroboration（kernel.verdict）
    → Evidence Write（kernel + repository）

降级铁律：搜索/抓取不可用时发 research_degraded 事件、claims 保持
unverified，绝不抛异常中断 Run（spec §21.1「搜索失败能够降级」）。
§15.2：搜索摘要只存 excerpt；产品事件只带计数与域名，不带摘要原文。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol
from urllib.parse import urlparse

from app.decision_harness.event_bridge import make_product_event
from app.decision_harness.evidence_kernel import Claim, EvidenceKernel
from app.decision_harness.repository import DecisionRunRepository
from app.decision_harness.schemas import (
    EvidenceKind,
    EvidenceRecordV4,
    EvidenceStatus,
)

_MAX_EXCERPT_CHARS = 800
_FETCH_TIMEOUT_SECONDS = 15.0
_SEARCH_TIMEOUT_SECONDS = 20.0


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    published_at: datetime | None = None


class SearchProvider(Protocol):
    """搜索/抓取抽象：Fake（测试）、Legacy（GLM web_search）、Noop（降级）。"""

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]: ...

    async def fetch(self, url: str) -> str | None:
        return None


class NoopSearchProvider:
    """永远不可用：触发降级路径。"""

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        raise RuntimeError("search provider unavailable")

    async def fetch(self, url: str) -> str | None:
        return None


class LegacyWebSearchProvider:
    """适配现有 GLM web_search 工具（同步 HTTP → to_thread）。"""

    def __init__(self, *, enable_fetch: bool = False):
        self._enable_fetch = enable_fetch

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        from app.tools.web_search import WebSearchError, web_search

        def _run() -> list[dict]:
            return web_search(query, max_results=max_results)

        try:
            results = await asyncio.to_thread(_run)
        except (WebSearchError, Exception):
            return []
        hits: list[SearchHit] = []
        for item in results:
            url = str(item.get("url") or item.get("link") or "").strip()
            if not url:
                continue
            hits.append(SearchHit(
                url=url,
                title=str(item.get("title") or "")[:200],
                snippet=str(item.get("content") or item.get("snippet") or ""),
            ))
        return hits

    async def fetch(self, url: str) -> str | None:
        if not self._enable_fetch:
            return None
        import urllib.request

        def _run() -> str:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible; ShenmoResearch/1.0)"}
            )
            with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_SECONDS) as resp:
                return resp.read(200_000).decode("utf-8", errors="replace")

        try:
            return await asyncio.to_thread(_run)
        except Exception:
            return None


class FakeSearchProvider:
    """确定性搜索（测试）：脚本化 query 关键词 → hits。"""

    def __init__(self, script: dict[str, list[SearchHit]] | None = None,
                 *, fail_queries: set[str] | None = None):
        self.script = script or {}
        self.fail_queries = fail_queries or set()
        self.search_calls: list[str] = []
        self.fetch_calls: list[str] = []

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        self.search_calls.append(query)
        if query in self.fail_queries:
            raise RuntimeError("simulated search failure")
        for keyword, hits in self.script.items():
            if keyword in query:
                return hits[:max_results]
        return []

    async def fetch(self, url: str) -> str | None:
        self.fetch_calls.append(url)
        return f"<html>原始来源正文 {url}</html>"


# 领域 → 查询模板（Source Plan 的程序化近似）
_QUERY_TEMPLATES = {
    "finance": "{claim} 官方数据 披露",
    "legal": "{claim} 法规 条文 官方",
    "medical": "{claim} 指南 研究",
    "company": "{claim} 公司 公告",
    "history": "{claim} 历史 档案 研究",
    "general": "{claim}",
}


@dataclass
class ResearchOutcome:
    claims_total: int = 0
    verified: int = 0
    corroborated: int = 0
    unverified: int = 0
    disputed: int = 0
    degraded: bool = False
    evidence_written: int = 0


class ResearchLead:
    """跑一轮 claim 驱动研究（§12.3 搜索 Loop）。"""

    def __init__(
        self,
        *,
        run_id: str,
        kernel: EvidenceKernel,
        repo: DecisionRunRepository,
        provider: SearchProvider,
        max_rounds: int = 2,
    ):
        self.run_id = run_id
        self.kernel = kernel
        self.repo = repo
        self.provider = provider
        self.max_rounds = max_rounds

    def _event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return make_product_event(self.run_id, event_type, payload)

    async def _persist(self, record: EvidenceRecordV4) -> None:
        await self.repo.upsert_evidence(
            self.run_id,
            record.id,
            claim_id=record.claim_id,
            kind=record.kind.value,
            status=record.status.value,
            source_title=record.source_title,
            source_url=record.source_url,
            source_domain=record.source_domain,
            excerpt=record.excerpt[:_MAX_EXCERPT_CHARS],
            relevance=record.relevance,
            reliability=record.reliability,
            authority_level=record.authority_level,
            raw={},
        )

    async def research_claims(
        self, claims: list[Claim]
    ) -> AsyncIterator[dict[str, Any]]:
        outcome = ResearchOutcome(claims_total=len(claims))
        for claim in claims:
            self.kernel.add_claim(claim)
        for claim in claims:
            template = _QUERY_TEMPLATES.get(claim.domain, _QUERY_TEMPLATES["general"])
            query = template.format(claim=claim.text)[:120]
            hits: list[SearchHit] = []
            degraded_this_claim = False
            for round_no in range(max(1, self.max_rounds)):
                try:
                    batch = await asyncio.wait_for(
                        self.provider.search(query, max_results=4),
                        timeout=_SEARCH_TIMEOUT_SECONDS,
                    )
                except Exception:
                    if round_no == 0:
                        outcome.degraded = True
                        degraded_this_claim = True
                    break
                hits.extend(batch)
                # 第一轮有结果即可（第二轮留给后续 follow-up 轮）
                if batch:
                    break

            if degraded_this_claim or not hits:
                yield self._event("run_log", {
                    "note": "research_degraded",
                    "claim_id": claim.claim_id,
                    "detail": "search_unavailable" if degraded_this_claim else "no_hits",
                })
                continue

            # 只保留不同域名的前 3 条（多样性优先于数量）
            seen_domains: set[str] = set()
            selected: list[SearchHit] = []
            for hit in hits:
                domain = urlparse(hit.url).netloc.lower()
                if domain in seen_domains:
                    continue
                seen_domains.add(domain)
                selected.append(hit)
                if len(selected) >= 3:
                    break

            for i, hit in enumerate(selected, start=1):
                evidence_id = f"ev-{claim.claim_id}-{i}"
                fetched = await self.provider.fetch(hit.url)
                record = EvidenceRecordV4(
                    id=evidence_id,
                    claim_id=claim.claim_id,
                    kind=EvidenceKind.WEB_SOURCE,
                    source_url=hit.url,
                    source_title=hit.title or hit.url,
                    excerpt=(fetched or hit.snippet or "")[:_MAX_EXCERPT_CHARS],
                    retrieved_at=datetime.now(timezone.utc),
                    supports_claim=claim.text[:500],
                )
                status = self.kernel.register(record, claim_id=claim.claim_id)
                stored = self.kernel.get(evidence_id) or record
                await self._persist(stored)
                outcome.evidence_written += 1
                if status in (EvidenceStatus.VERIFIED, EvidenceStatus.CORROBORATED):
                    yield self._event("evidence_added", {
                        "claim_id": claim.claim_id,
                        "evidence_id": evidence_id,
                        "source_domain": stored.source_domain,
                        "status": status.value,
                    })
                else:
                    yield self._event("evidence_rejected", {
                        "claim_id": claim.claim_id,
                        "evidence_id": evidence_id,
                        "reason": status.value,
                    })

            verdict = self.kernel.verdict(claim.claim_id)
            if verdict.status == EvidenceStatus.CORROBORATED:
                outcome.corroborated += 1
            elif verdict.status == EvidenceStatus.VERIFIED:
                outcome.verified += 1
            elif verdict.status == EvidenceStatus.DISPUTED:
                outcome.disputed += 1
            else:
                outcome.unverified += 1
            yield self._event("run_log", {
                "note": "claim_verdict",
                "claim_id": claim.claim_id,
                "status": verdict.status.value,
                "detail": verdict.note,
            })

        self.outcome = outcome
        yield self._event("run_log", {
            "note": "research_complete",
            "claims": outcome.claims_total,
            "verified": outcome.verified,
            "corroborated": outcome.corroborated,
            "unverified": outcome.unverified,
            "degraded": outcome.degraded,
        })
