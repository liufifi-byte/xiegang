"""Phase 6 测试：Evidence Auditor、Finalization Gate、最终报告与 PARTIAL 路径。"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.decision_harness.evidence_kernel import Claim, EvidenceKernel
from app.decision_harness.finalization import (
    EvidenceAuditor,
    GateInput,
    build_final_report,
    evaluate_gate,
)
from app.decision_harness.repository import (
    DecisionRunRepository,
    _clear_memory_for_tests,
)
from app.decision_harness.schemas import (
    BudgetPolicy,
    CompletionState,
    DecisionModel,
    EvidenceKind,
    EvidenceRecordV4,
    EvidenceStatus,
    FinalReport,
    PlanSpec,
    Recommendation,
    RunExitStatus,
    SubAgentResult,
)
from app.decision_harness.workflow import DecisionWorkflow

NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)
DECISION = DecisionModel(
    question="要不要从大厂跳去创业公司",
    options=["跳槽", "留下"],
    constraints=["现金流最多撑 12 个月"],
    unknowns=["期权行权条款"],
)
PLAN = PlanSpec(
    plan_version=1, objective="两周内完成决策评估",
    expert_tasks=["economist: 现金流压力测试"],
    blocking_unknowns=["期权行权条款"],
    created_from_state_version=1,
)


@pytest.fixture(autouse=True)
def _clean():
    _clear_memory_for_tests()
    yield
    _clear_memory_for_tests()


def _result(agent, stance, judgment, **over):
    body = dict(
        task_id=f"task-{agent}-1", agent_id=agent, status="completed",
        stance=stance, judgment=judgment, confidence=0.8,
        action="拿到书面期权条款前不辞职",
        reversal_conditions=["存款补充 50 万"],
    )
    body.update(over)
    return SubAgentResult(**body)


def _report(**over):
    report = FinalReport(
        recommendation=Recommendation(
            option="跳槽", strength="clear", summary="窗口与现金流权衡后的结论",
        ),
        action_plan=["拿到书面期权条款前不辞职"],
        completion=CompletionState(status=RunExitStatus.FINALIZED),
    )
    return report.model_copy(update=over) if over else report


# ============ Evidence Auditor ============

async def _collect(gen):
    return [e async for e in gen]


async def test_auditor_rejects_stale_and_low_reliability():
    kernel = EvidenceKernel(run_id="r1")
    kernel.add_claim(Claim(claim_id="claim-1", text="期权条款", domain="finance"))
    kernel.register(EvidenceRecordV4(
        id="ev-stale", claim_id="claim-1", kind=EvidenceKind.WEB_SOURCE,
        source_url="https://www.sec.gov/old",
        published_at=NOW - timedelta(days=500),  # finance 窗口外
        retrieved_at=NOW,
    ), claim_id="claim-1")
    kernel.register(EvidenceRecordV4(
        id="ev-good", claim_id="claim-1", kind=EvidenceKind.WEB_SOURCE,
        source_url="https://www.gov.cn/policy", retrieved_at=NOW,
    ), claim_id="claim-1")
    # 非 web 类证据不经权威下限抬升，低可靠性原样保留 → 审计拒绝
    kernel.register(EvidenceRecordV4(
        id="ev-low", claim_id="claim-1", kind=EvidenceKind.INFERENCE,
        reliability=0.1,
    ), claim_id="claim-1")

    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("r1", runtime_mode="x", decision_state={}, budget={})
    auditor = EvidenceAuditor(run_id="r1", kernel=kernel, repo=repo,
                              core_claim_ids=["claim-1"])
    events = await _collect(auditor.run())

    types = [e["event_type"] for e in events]
    assert types[0] == "audit_started"
    assert "evidence_rejected" in types and "audit_failed" in types
    rejected = {e["payload"]["evidence_id"] for e in events
                if e["event_type"] == "evidence_rejected"}
    assert "ev-stale" in rejected and "ev-low" in rejected
    assert kernel.get("ev-stale").status == EvidenceStatus.REJECTED
    rows = {r["id"]: r for r in await repo.list_evidence("r1")}
    assert rows["ev-stale"]["status"] == "rejected"
    # 核心 claim 仍有强支撑（gov.cn）→ 不在 unsupported 列表
    assert auditor.outcome.unsupported_core_claims == []
    assert auditor.outcome.passed is False  # 有拒绝即不通过


# ============ Gate ============

def _gate_input(**over):
    inp = dict(
        decision_options=["跳槽", "留下"],
        results=[
            _result("economist", "oppose", "跳槽现金流风险高，留下更稳"),
            _result("strategist", "support", "跳槽窗口难得"),
        ],
        open_disputes=0,
        needs_user=False,
        audit_passed=True,
        pending_tasks=0,
        report=_report(),
        blocking_unknowns=[],
        unverified_unknowns=[],
    )
    inp.update(over)
    return GateInput(**inp)


def test_gate_full_pass_finalized():
    gate = evaluate_gate(_gate_input(
        results=[_result("economist", "oppose", "跳槽与留下各有代价"),
                 _result("strategist", "support", "跳槽窗口与留下 Stability")],
    ))
    assert gate.passed and gate.status == RunExitStatus.FINALIZED


def test_gate_failures_go_partial():
    g = evaluate_gate(_gate_input(open_disputes=1))
    assert g.status == RunExitStatus.PARTIAL_FINALIZED
    assert "material_conflicts_handled" in g.failures

    g2 = evaluate_gate(_gate_input(pending_tasks=2))
    assert g2.status == RunExitStatus.PARTIAL_FINALIZED
    assert "no_pending_high_priority_task" in g2.failures

    g3 = evaluate_gate(_gate_input(
        blocking_unknowns=["期权行权条款"],
        unverified_unknowns=["期权行权条款"],
    ))
    assert "critical_gaps_handled" in g3.failures


def test_gate_needs_user_wins_over_partial():
    g = evaluate_gate(_gate_input(needs_user=True, open_disputes=1))
    assert g.status == RunExitStatus.WAITING_USER


def test_gate_budget_exhausted_is_partial_not_failed():
    g = evaluate_gate(_gate_input(), budget_exhausted=True)
    assert g.status == RunExitStatus.PARTIAL_FINALIZED
    assert g.failures == []  # 条件全过，仅预算受限


# ============ 报告构造 ============

def test_report_partial_has_limitations_and_reversal():
    report = build_final_report(
        decision_options=["跳槽", "留下"],
        results=[_result("economist", "cautious", "现金流可支撑")],
        disputes=[{"question": "风险偏好", "status": "retained"}],
        unknowns=["期权条款"],
        gate_failures=["material_conflicts_handled"],
        partial=True,
    )
    assert report.recommendation.strength == "conditional"
    assert report.completion.status == RunExitStatus.PARTIAL_FINALIZED
    assert "material_conflicts_handled" in report.completion.limitations
    assert report.reversal_conditions == ["存款补充 50 万"]
    assert report.remaining_uncertainties  # 保留争议进入不确定清单


def test_report_insufficient_requires_uncertainties():
    report = build_final_report(
        decision_options=["跳槽"], results=[], disputes=[], unknowns=[],
        gate_failures=[], partial=True,
    )
    assert report.recommendation.strength == "insufficient"
    # §18 契约：insufficient 必须有 remaining_uncertainties（schema 强制）
    with pytest.raises(Exception):
        FinalReport.model_validate(report.model_dump(
            update={"remaining_uncertainties": []}))


# ============ Workflow run_finalize ============

async def _wf_with_results(repo, results, *, disputes=None, decision=DECISION,
                           plan=PLAN):
    wf = DecisionWorkflow(
        run_id="r1", decision=decision,
        budget=BudgetPolicy(max_main_actions=3, max_consecutive_idle_turns=2),
        runtime=__import__("app.decision_harness", fromlist=["FakeAgentRuntime"]).FakeAgentRuntime(),
        repo=repo,
    )
    state = {"disputes": disputes or []}
    if plan:
        state["active_plan_dict"] = None
    await repo.create_run("r1", runtime_mode="x",
                          decision_state=state, budget={})
    if plan:
        await repo.update_run("r1", active_plan=plan.model_dump(mode="json"))
    wf.expert_results = results
    return wf


async def test_finalize_happy_path():
    repo = DecisionRunRepository(force_memory=True)
    wf = await _wf_with_results(repo, [
        _result("economist", "oppose", "留下更稳：现金流容错不足"),
        _result("strategist", "support", "跳槽窗口难得但可等半年"),
    ], disputes=[{"question": "风险", "status": "resolved",
                  "participants": ["economist", "strategist"]}])
    events = await _collect(wf.run_finalize())

    assert wf.exit_status == RunExitStatus.FINALIZED
    types = [e["event_type"] for e in events]
    assert "finalization_proposed" in types and "decision_finalized" in types
    row = await repo.get_run("r1")
    assert row["status"] == "finalized"
    assert row["exit_status"] == "FINALIZED"
    fr = row["decision_state"]["final_report"]
    assert fr["recommendation"]["strength"] in ("clear", "conditional")
    assert wf.final_report.recommendation.summary


async def test_finalize_budget_exhausted_partial():
    repo = DecisionRunRepository(force_memory=True)
    wf = await _wf_with_results(repo, [_result("economist", "cautious", "有限结论")])
    events = await _collect(wf.run_finalize(budget_exhausted=True))
    assert wf.exit_status == RunExitStatus.PARTIAL_FINALIZED
    assert any(e["event_type"] == "partial_finalized" for e in events)
    row = await repo.get_run("r1")
    assert row["status"] == "partial_finalized"
    assert row["decision_state"]["final_report"]["completion"]["status"] == "PARTIAL_FINALIZED"


async def test_finalize_cancelled_preserves_results():
    repo = DecisionRunRepository(force_memory=True)
    wf = await _wf_with_results(repo, [_result("economist", "support", "结论已出")])
    events = await _collect(wf.run_finalize(cancel_requested=True))
    assert wf.exit_status == RunExitStatus.CANCELLED
    row = await repo.get_run("r1")
    assert row["status"] == "cancelled"
    assert any(e["payload"].get("note") == "run_cancelled" for e in events)
    # 已完成结果保留
    assert row["decision_state"]["final_report"]["expert_findings"]


async def test_planning_budget_exhaustion_is_partial_not_failed():
    """spec Phase 6 任务 4：移除预算耗尽直接 failed 的路径。"""
    from app.decision_harness.runtime import FakeAgentRuntime, RuntimeEvent
    sid = "r1:main"
    garbage = RuntimeEvent(seq=0, type="turn_final", payload={"final_response": "嗯"})
    fake = FakeAgentRuntime(scripts={sid: [[garbage]] * 3})
    repo = DecisionRunRepository(force_memory=True)
    wf = DecisionWorkflow(
        run_id="r1", decision=DECISION,
        budget=BudgetPolicy(max_main_actions=2, max_consecutive_idle_turns=9),
        runtime=fake, repo=repo,
    )
    events = await _collect(wf.run())
    assert wf.outcome.status == "partial_finalized"
    assert any(e["event_type"] == "partial_finalized" for e in events)
    row = await repo.get_run("r1")
    assert row["status"] == "partial_finalized"
    assert row["exit_status"] == "PARTIAL_FINALIZED"
    assert row["finalization_state"]["budget_exhausted"] is True
