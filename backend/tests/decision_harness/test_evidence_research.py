"""Phase 4 测试：Evidence Kernel 状态机 + Research Lead 搜索循环与降级。"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.decision_harness.evidence_kernel import (
    Claim,
    EvidenceKernel,
    classify_authority,
    is_stale,
)
from app.decision_harness.research_lead import (
    FakeSearchProvider,
    NoopSearchProvider,
    ResearchLead,
    SearchHit,
)
from app.decision_harness.repository import (
    DecisionRunRepository,
    _clear_memory_for_tests,
)
from app.decision_harness.schemas import (
    EvidenceKind,
    EvidenceRecordV4,
    EvidenceStatus,
)

NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean():
    _clear_memory_for_tests()
    yield
    _clear_memory_for_tests()


def _web_record(eid: str, url: str, claim_id="claim-1", **over) -> EvidenceRecordV4:
    return EvidenceRecordV4(
        id=eid, claim_id=claim_id, kind=EvidenceKind.WEB_SOURCE,
        source_url=url, retrieved_at=NOW, **over,
    )


# ============ Kernel：权威 / 时效 / 交叉验证 ============

def test_classify_authority_domain_routing():
    assert classify_authority("https://www.stats.gov.cn/x") == "official"
    assert classify_authority("https://www.nature.com/article") == "authoritative"
    assert classify_authority("https://www.caixin.com/a") == "media"
    assert classify_authority("https://zhihu.com/question/1") == "community"
    assert classify_authority("https://example.orgrandom.net/") in ("authoritative", "unknown")


def test_register_scores_authority_and_verifies():
    kernel = EvidenceKernel(run_id="r1")
    kernel.add_claim(Claim(claim_id="claim-1", text="平均薪酬数据", domain="finance"))
    status = kernel.register(
        _web_record("ev1", "https://www.stats.gov.cn/salary"), claim_id="claim-1"
    )
    assert status == EvidenceStatus.VERIFIED
    rec = kernel.get("ev1")
    assert rec.authority_level == "official"
    assert rec.reliability >= 0.9

    verdict = kernel.verdict("claim-1")
    assert verdict.status == EvidenceStatus.VERIFIED
    assert verdict.evidence_ids == ["ev1"]


def test_low_authority_only_never_verifies():
    """§12.2：社区来源不能成为唯一依据。"""
    kernel = EvidenceKernel(run_id="r1")
    kernel.add_claim(Claim(claim_id="claim-1", text="某公司传闻"))
    kernel.register(_web_record("ev1", "https://zhihu.com/q/1"), claim_id="claim-1")
    kernel.register(_web_record("ev2", "https://weibo.com/x"), claim_id="claim-1")
    verdict = kernel.verdict("claim-1")
    assert verdict.status == EvidenceStatus.UNVERIFIED
    assert verdict.note == "low_authority_only"


def test_corroboration_requires_two_domains():
    kernel = EvidenceKernel(run_id="r1")
    kernel.add_claim(Claim(claim_id="c", text="政策生效日期"))
    kernel.register(_web_record("ev1", "https://www.gov.cn/policy"), claim_id="c")
    verdict = kernel.verdict("c")
    assert verdict.status == EvidenceStatus.VERIFIED  # 单一官方来源
    kernel.register(_web_record("ev2", "https://www.reuters.com/policy"), claim_id="c")
    verdict = kernel.verdict("c")
    assert verdict.status == EvidenceStatus.CORROBORATED  # 两个独立域名


def test_stale_by_domain_window():
    old = _web_record(
        "ev1", "https://www.sec.gov/filing",
        published_at=NOW - timedelta(days=400),  # finance 窗口 365 天
    )
    kernel = EvidenceKernel(run_id="r1")
    kernel.add_claim(Claim(claim_id="c", text="财报", domain="finance"))
    status = kernel.register(old, claim_id="c")
    assert status == EvidenceStatus.STALE  # 时效检查在 register 内完成
    assert is_stale(old, domain="finance", now=NOW)
    # 历史领域窗口 10 年：同一条不过期
    assert not is_stale(old, domain="history", now=NOW)


def test_disputed_when_contradicting():
    kernel = EvidenceKernel(run_id="r1")
    kernel.add_claim(Claim(claim_id="c", text="X 优于 Y"))
    kernel.register(_web_record("ev1", "https://www.gov.cn/a"), claim_id="c")
    kernel.register(_web_record(
        "ev2", "https://www.reuters.com/b", contradicts_claim="数据显示相反",
    ), claim_id="c")
    assert kernel.verdict("c").status == EvidenceStatus.DISPUTED


def test_usable_summary_excludes_excerpt():
    kernel = EvidenceKernel(run_id="r1")
    kernel.add_claim(Claim(claim_id="c", text="t"))
    kernel.register(_web_record(
        "ev1", "https://www.gov.cn/a", excerpt="很长的摘要正文" * 50,
    ), claim_id="c")
    kernel.register(_web_record("ev2", "https://zhihu.com/x"), claim_id="c")  # 低权威
    summary = kernel.usable_summary()
    assert len(summary) == 1 and summary[0]["id"] == "ev1"
    blob = json.dumps(summary, ensure_ascii=False)
    assert "摘要正文" not in blob  # §15.2：excerpt 不外泄
    assert summary[0]["source_url"].startswith("https://www.gov.cn")


# ============ Research Lead：循环与降级 ============

async def _collect(gen):
    return [e async for e in gen]


async def test_research_happy_path_writes_evidence():
    kernel = EvidenceKernel(run_id="r1")
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("r1", runtime_mode="x", decision_state={}, budget={})
    provider = FakeSearchProvider(script={
        "薪酬": [
            SearchHit(url="https://www.stats.gov.cn/salary", title="统计局数据"),
            SearchHit(url="https://www.stats.gov.cn/other", title="同域第二条"),
            SearchHit(url="https://www.reuters.com/salary", title="路透报道"),
            SearchHit(url="https://zhihu.com/q", title="知乎讨论"),
        ],
    })
    lead = ResearchLead(run_id="r1", kernel=kernel, repo=repo, provider=provider)
    events = await _collect(lead.research_claims([
        Claim(claim_id="claim-1", text="行业平均薪酬", domain="finance"),
    ]))

    types = [e["event_type"] for e in events]
    assert "evidence_added" in types
    # 域名多样性：同域去重后 3 条（gov + reuters + zhihu）
    rows = await repo.list_evidence("r1")
    assert len(rows) == 3
    added = [e["payload"] for e in events if e["event_type"] == "evidence_added"]
    assert any(p["source_domain"] == "www.stats.gov.cn" for p in added)
    # §15.2：事件不带摘要原文
    assert all("snippet" not in p and "excerpt" not in p for p in added)
    # claim 判定：gov + reuters 双域 → corroborated
    verdict_events = [e for e in events if e["payload"].get("note") == "claim_verdict"]
    assert verdict_events[0]["payload"]["status"] == "corroborated"
    assert lead.outcome.evidence_written == 3


async def test_research_degrades_without_crashing():
    kernel = EvidenceKernel(run_id="r1")
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("r1", runtime_mode="x", decision_state={}, budget={})
    lead = ResearchLead(
        run_id="r1", kernel=kernel, repo=repo, provider=NoopSearchProvider(),
    )
    events = await _collect(lead.research_claims([
        Claim(claim_id="claim-1", text="某政策细节"),
    ]))
    notes = [e["payload"].get("note") for e in events]
    assert "research_degraded" in notes
    assert kernel.verdict("claim-1").status == EvidenceStatus.UNVERIFIED
    assert await repo.list_evidence("r1") == []
    assert lead.outcome.degraded is True


async def test_research_partial_provider_failure():
    """单条查询失败只影响该 claim，其余继续。"""
    kernel = EvidenceKernel(run_id="r1")
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("r1", runtime_mode="x", decision_state={}, budget={})
    provider = FakeSearchProvider(
        script={"政策": [SearchHit(url="https://www.gov.cn/p", title="官方")]},
        fail_queries=set(),  # 通过空结果模拟另一条无命中
    )
    lead = ResearchLead(run_id="r1", kernel=kernel, repo=repo, provider=provider)
    events = await _collect(lead.research_claims([
        Claim(claim_id="claim-1", text="政策条文", domain="legal"),
        Claim(claim_id="claim-2", text="完全查不到的东西xyzzy"),
    ]))
    assert kernel.verdict("claim-1").status == EvidenceStatus.VERIFIED
    assert kernel.verdict("claim-2").status == EvidenceStatus.UNVERIFIED
    notes = [e["payload"].get("note") for e in events]
    assert notes.count("research_degraded") == 1
