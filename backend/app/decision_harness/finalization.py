"""Evidence Audit 与 Finalization Gate（spec 2026-08-22 §7.5/§12.4/§16/§18，Phase 6）。

Evidence Auditor：核验支撑关键结论的证据（URL 可打开性由注册时保证，
此处查状态/权威/可靠性），不合格即拒绝并重算 claim 判定（定向修订）。

Finalization Gate：主 Agent 只能"提议结束"——Gate 按 §7.5 八条件裁决：
    FINALIZED        全部通过
    PARTIAL_FINALIZED 预算耗尽或条件未齐，但已有安全有限结论
                       （§16：硬预算不得直接标记失败）
    WAITING_USER     缺失会反转建议的用户信息
    CANCELLED        用户主动停止，保留已完成结果
    FAILED           仅核心运行时不可恢复（本模块不产生）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.decision_harness.event_bridge import make_product_event
from app.decision_harness.evidence_kernel import EvidenceKernel
from app.decision_harness.repository import DecisionRunRepository
from app.decision_harness.schemas import (
    CompletionState,
    EvidenceStatus,
    FinalReport,
    Recommendation,
    RunExitStatus,
    SubAgentResult,
)

_MIN_RELIABILITY = 0.3
_CORE_LOW_AUTHORITY = {"community", "unknown"}


@dataclass
class AuditOutcome:
    passed: bool = True
    issues: list[str] = field(default_factory=list)
    rejected_ids: list[str] = field(default_factory=list)
    unsupported_core_claims: list[str] = field(default_factory=list)


class EvidenceAuditor:
    """§12.4 引用审计 + 定向修订。用法与 Runner 一致：跑完读 .outcome。"""

    def __init__(
        self,
        *,
        run_id: str,
        kernel: EvidenceKernel,
        repo: DecisionRunRepository,
        core_claim_ids: list[str] | None = None,
    ):
        self.run_id = run_id
        self.kernel = kernel
        self.repo = repo
        self.core_claim_ids = core_claim_ids or []
        self.outcome = AuditOutcome()

    async def run(self) -> AsyncIterator[dict[str, Any]]:
        kernel, repo, run_id = self.kernel, self.repo, self.run_id
        outcome = self.outcome
        yield make_product_event(run_id, "audit_started",
                                 {"records": len(kernel.records)})

        for record in kernel.records:
            kind = getattr(record.kind, "value", record.kind)
            reason = ""
            if kind == "web_source":
                if not record.source_url:
                    reason = "web_source_without_url"
                elif record.status == EvidenceStatus.STALE:
                    reason = "stale"
            if not reason and record.reliability < _MIN_RELIABILITY:
                reason = "low_reliability"
            if reason:
                kernel.mark_rejected(record.id, reason)
                outcome.rejected_ids.append(record.id)
                outcome.passed = False
                outcome.issues.append(f"{record.id}:{reason}")
                await repo.upsert_evidence(
                    run_id, record.id,
                    claim_id=record.claim_id,
                    kind=str(kind),
                    status=EvidenceStatus.REJECTED.value,
                    source_title=record.source_title,
                    source_url=record.source_url,
                    source_domain=record.source_domain,
                    reliability=record.reliability,
                    authority_level=record.authority_level,
                    raw={"reject_reason": reason},
                )
                yield make_product_event(run_id, "evidence_rejected", {
                    "claim_id": record.claim_id,
                    "evidence_id": record.id,
                    "reason": reason,
                })

        # 定向修订：核心 claim 在剔除后必须仍有强支撑
        for claim_id in self.core_claim_ids:
            verdict = kernel.verdict(claim_id)
            if verdict.status not in (EvidenceStatus.VERIFIED, EvidenceStatus.CORROBORATED):
                strong_left = [
                    r for r in kernel.evidence_for_claim(claim_id)
                    if r.status in (EvidenceStatus.VERIFIED, EvidenceStatus.CORROBORATED)
                    and r.authority_level not in _CORE_LOW_AUTHORITY
                ]
                if not strong_left:
                    outcome.unsupported_core_claims.append(claim_id)
                    outcome.passed = False
                    outcome.issues.append(f"core_claim_unsupported:{claim_id}")

        yield make_product_event(
            run_id,
            "audit_passed" if outcome.passed else "audit_failed",
            {"rejected": len(outcome.rejected_ids),
             "unsupported_core_claims": outcome.unsupported_core_claims,
             "issues": outcome.issues[:10]},
        )


@dataclass
class GateInput:
    decision_options: list[str]
    results: list[SubAgentResult]
    open_disputes: int
    needs_user: bool
    audit_passed: bool
    pending_tasks: int
    report: FinalReport
    blocking_unknowns: list[str] = field(default_factory=list)
    unverified_unknowns: list[str] = field(default_factory=list)


@dataclass
class GateDecision:
    status: RunExitStatus
    failures: list[str]

    @property
    def passed(self) -> bool:
        return self.status == RunExitStatus.FINALIZED


def evaluate_gate(inp: GateInput, *, budget_exhausted: bool = False) -> GateDecision:
    """§7.5 八条件裁决。任一不满足 → PARTIAL_FINALIZED（或 WAITING_USER）。"""
    failures: list[str] = []

    completed = [r for r in inp.results
                 if r.status in ("completed", "partial") and r.judgment.strip()]
    if not completed:
        failures.append("objective_answered")
    else:
        answered_ids = {r.agent_id for r in completed}

    blob = " ".join(r.judgment + " ".join(r.analysis_points) for r in inp.results)
    missing_options = [o for o in inp.decision_options if o and o not in blob]
    if inp.decision_options and missing_options:
        failures.append("options_covered")

    unhandled = [u for u in inp.blocking_unknowns if u in inp.unverified_unknowns]
    if unhandled:
        failures.append("critical_gaps_handled")

    if inp.open_disputes:
        failures.append("material_conflicts_handled")

    if not inp.audit_passed:
        failures.append("evidence_audit_passed")

    if not inp.report.action_plan:
        failures.append("recommendation_actionable")

    if inp.pending_tasks:
        failures.append("no_pending_high_priority_task")

    try:
        FinalReport.model_validate(inp.report.model_dump())
    except Exception:
        failures.append("output_contract_valid")

    if inp.needs_user:
        return GateDecision(RunExitStatus.WAITING_USER, ["missing_user_input"])
    if budget_exhausted or failures:
        return GateDecision(RunExitStatus.PARTIAL_FINALIZED, failures)
    return GateDecision(RunExitStatus.FINALIZED, [])


def build_final_report(
    *,
    decision_options: list[str],
    results: list[SubAgentResult],
    disputes: list[dict[str, Any]],
    unknowns: list[str],
    gate_failures: list[str],
    partial: bool,
) -> FinalReport:
    """从结构化状态合成最终报告（§18）。文案层可重组，结构化事实不得改写。"""
    speaking = [r for r in results
                if r.status in ("completed", "partial") and r.judgment.strip()]
    retained = [d for d in disputes if d.get("status") == "retained"]
    narrowed = [d for d in disputes if d.get("status") == "narrowed"]

    if not speaking:
        strength = "insufficient"
        option, summary = (
            (decision_options[0] if decision_options else "暂无可推荐选项"),
            "专家阶段未产出可用结论，无法形成建议。",
        )
    else:
        # 立场加权（confidence × 1）：支持/反对各自的权重和
        support = sum(r.confidence for r in speaking if r.stance == "support")
        oppose = sum(r.confidence for r in speaking if r.stance == "oppose")
        if support > oppose:
            best = max((r for r in speaking if r.stance == "support"),
                       key=lambda r: r.confidence)
        elif oppose > support:
            best = max((r for r in speaking if r.stance == "oppose"),
                       key=lambda r: r.confidence)
        else:
            best = max(speaking, key=lambda r: r.confidence)
        option = best.judgment[:200]
        if partial or gate_failures or retained or unknowns:
            strength = "conditional"
        else:
            strength = "clear"
        summary = f"{best.judgment}（权重立场：支持 {support:.1f} / 反对 {oppose:.1f}）"

    remaining = list(dict.fromkeys(
        unknowns
        + [f"争议未决：{d.get('question', '')[:60]}" for d in retained]
        + [f"待确认变量：{d.get('question', '')[:60]}" for d in narrowed]
    ))[:10]
    if strength == "insufficient" and not remaining:
        # §18 契约：insufficient 必须列 remaining_uncertainties
        remaining = ["专家阶段未产出可用结论，关键信息缺口未补齐"]

    return FinalReport(
        recommendation=Recommendation(
            option=option,
            strength=strength,
            summary=summary,
            applicable_conditions=[
                f"{d.get('missing_variable') or d.get('question', '')[:60]}"
                for d in narrowed if d.get("missing_variable")
            ][:8],
        ),
        option_comparison=[
            {"option": o, "judgments": [
                r.judgment[:200] for r in speaking if o in r.judgment
            ]}
            for o in decision_options
        ],
        expert_findings=[
            {"agent_id": r.agent_id, "stance": r.stance,
             "judgment": r.judgment, "confidence": r.confidence,
             "status": r.status}
            for r in results
        ],
        resolved_disputes=[d for d in disputes if d.get("status") == "resolved"],
        remaining_disagreements=[d for d in disputes if d.get("status") != "resolved"],
        key_evidence=[],
        remaining_uncertainties=remaining,
        action_plan=[
            r.action for r in speaking if r.action
        ][:10],
        risk_controls=[],
        reversal_conditions=list(dict.fromkeys(
            rc for r in speaking for rc in r.reversal_conditions
        ))[:10],
        completion=CompletionState(
            status=RunExitStatus.PARTIAL_FINALIZED if partial else RunExitStatus.FINALIZED,
            limitations=(gate_failures + (["预算受限"] if partial else []))[:8],
        ),
    )
