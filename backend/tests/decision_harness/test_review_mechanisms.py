"""评审四机制测试：数字账本 / 结论卡 / 统一追问关口 / 辩论收紧。"""
import json

import pytest

from app.decision_harness.number_ledger import (
    NumberLedger,
    collect_from_results,
    normalize_label,
)
from app.decision_harness.repository import (
    DecisionRunRepository,
    _clear_memory_for_tests,
)
from app.decision_harness.runtime import FakeAgentRuntime, RuntimeEvent
from app.decision_harness.schemas import (
    BudgetPolicy,
    DecisionModel,
    PlanSpec,
    SubAgentResult,
)
from app.decision_harness.workflow import DecisionWorkflow

DECISION = DecisionModel(
    question="要不要在海淀买 1500 万的房子",
    options=["买房", "继续租房"],
    constraints=["首付 1000 万，贷款 500 万"],
)


@pytest.fixture(autouse=True)
def _clean():
    _clear_memory_for_tests()
    yield
    _clear_memory_for_tests()


def _final(payload: dict) -> RuntimeEvent:
    return RuntimeEvent(
        seq=0, type="turn_final",
        payload={"final_response": json.dumps(payload, ensure_ascii=False)},
    )


# ============ 机制一：数字账本 ============

def test_label_normalization_and_value_matching():
    assert normalize_label("家庭月收入") == "税后月收入"
    assert normalize_label("每月还款额") == "月供"
    ledger = NumberLedger()
    ledger.register("月供", "2.1万", basis="calculation", source_agent="mathematician")
    ledger.register("月供", 21000, basis="calculation", source_agent="economist")
    assert ledger.unresolved_conflicts() == []  # 2.1万 == 21000 不算冲突


def test_conflict_detection_and_prompt_snapshot():
    ledger = NumberLedger()
    ledger.register("月供占收入比", "42%", basis="calculation",
                    source_agent="mathematician", note="商贷")
    ledger.register("月供占收入比", "35%", basis="calculation",
                    source_agent="mathematician", note="公积金组合贷")
    ledger.register("年化机会成本", "30万", basis="assumption",
                    source_agent="economist", note="口径未确认")
    conflicts = ledger.unresolved_conflicts()
    assert len(conflicts) == 1 and conflicts[0].label == "月供占收入比"
    snapshot = ledger.snapshot_for_prompt()
    assert "会议公认数字表" in snapshot
    assert "月供占收入比" in snapshot
    assert "数字冲突待对账" in snapshot and "42%" in snapshot
    ledger.mark_resolved("月供占收入比")
    assert ledger.unresolved_conflicts() == []


def test_collect_from_results_catches_contradiction():
    """评审实录：经济学家两轮数字自相矛盾（200万 vs 120-175万量级）。"""
    r1 = SubAgentResult(
        task_id="t1", agent_id="economist", status="completed",
        judgment="年净资本成本约 20 万", numbers=[
            {"label": "年化资本成本", "value": "20万", "basis": "calculation"},
        ],
    )
    r2 = SubAgentResult(
        task_id="t2", agent_id="mathematician", status="completed",
        judgment="5 年亏损超 200 万", numbers=[
            {"label": "年化资本成本", "value": "40万", "basis": "calculation"},
        ],
    )
    ledger = collect_from_results([r1, r2])
    assert any(c.label == "年化机会成本" or c.label == "年化资本成本"
               for c in ledger.unresolved_conflicts())


# ============ 机制二：steelman/数字对账 1 轮 + 回应硬规则 ============

def test_debate_prompt_contains_hard_rules():
    import inspect
    from app.decision_harness.debate_loop import DebateRunner

    src = inspect.getsource(DebateRunner._packet_prompt)
    assert "回应硬规则" in src
    assert "不交锋的模板句" in src


async def test_number_conflict_opens_reconciliation_dispute():
    """数字冲突 → dispute-number 争议，且最多 1 轮。"""
    eco_sid = "run1:expert:task-economist-1"
    math_sid = "run1:expert:task-mathematician-2"
    fake = FakeAgentRuntime(scripts={
        eco_sid: [
            [_final({"status": "completed", "stance": "cautious",
                     "judgment": "月供占比 42%", "confidence": 0.7,
                     "numbers": [{"label": "月供占收入比", "value": "42%",
                                  "basis": "calculation"}],
                     "questions_for_user": ["年薪是税前还是税后？"]})],
            [_final({"dispute_id": "dispute-number-1", "agent_id": "economist",
                     "position_before": "cautious", "position_after": "cautious",
                     "changed": False, "remaining_disagreement": "口径未对齐",
                     "new_evidence_refs": ["新口径"]})],
        ],
        math_sid: [
            [_final({"status": "completed", "stance": "cautious",
                     "judgment": "月供占比 35%（组合贷）", "confidence": 0.7,
                     "numbers": [{"label": "月供占收入比", "value": "35%",
                                  "basis": "calculation"}]})],
            [_final({"dispute_id": "dispute-number-1", "agent_id": "mathematician",
                     "position_before": "cautious", "position_after": "cautious",
                     "changed": False, "remaining_disagreement": "维持",
                     "new_evidence_refs": []})],
        ],
        "run1:main": [[_final({"ack": 1})]] * 2,
    })
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="x", decision_state={}, budget={})
    plan = PlanSpec(plan_version=1, objective="评估购房财务可行性",
                    expert_tasks=["economist: 机会成本", "mathematician: 阈值测算"],
                    created_from_state_version=1)
    await repo.update_run("run1", active_plan=plan.model_dump(mode="json"))
    wf = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BudgetPolicy(),
                          runtime=fake, repo=repo, auto_continue_without_user=True)
    # 先跑专家（账本在这里生成）
    async for _ in wf.run_experts(allowed_experts=("economist", "mathematician")):
        pass
    assert getattr(wf, "number_ledger", None) is not None
    assert any(c.label == "月供占收入比" for c in wf.number_ledger.unresolved_conflicts())

    # 辩论：数字对账争议自动开启
    events = [e async for e in wf.run_debate()]
    notes = [e["payload"].get("note") for e in events if e["event_type"] == "run_log"]
    assert "number_reconciliation_opened" in notes
    disputes = (await repo.get_run("run1"))["decision_state"].get("disputes") or []
    assert any(d["dispute_id"] == "dispute-number-1" for d in disputes)
    assert all(d["rounds"] <= 1 for d in disputes
               if d["dispute_id"].startswith("dispute-number-"))


# ============ 机制：统一追问关口 ============

async def test_question_gate_waits_user_before_debate():
    """生产模式：有反转变量问题时辩论前 WAITING_USER；影子模式继续。"""
    results = [
        SubAgentResult(task_id="t1", agent_id="economist", status="completed",
                       stance="cautious", judgment="现金流紧",
                       questions_for_user=["年薪 60 万是税前还是税后？"]),
        SubAgentResult(task_id="t2", agent_id="sociologist", status="completed",
                       stance="cautious", judgment="家庭绑定"),
    ]
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="x", decision_state={}, budget={})
    wf = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BudgetPolicy(),
                          runtime=FakeAgentRuntime(), repo=repo)  # 生产模式
    events = [e async for e in wf.run_debate(results=results)]
    asked = [e for e in events if e["event_type"] == "user_input_requested"]
    assert asked and "税前还是税后" in asked[0]["payload"]["questions"][0]
    row = await repo.get_run("run1")
    assert row["status"] == "waiting_user"
    assert wf.pending_user_questions


# ============ 机制四：结论卡 ============

async def test_chair_summary_card_structure():
    from app.decision_harness.finalization import build_final_report

    report = build_final_report(
        decision_options=["买房", "租房"],
        results=[
            SubAgentResult(task_id="t1", agent_id="economist", status="completed",
                           stance="cautious", judgment="可承受但偏紧",
                           confidence=0.7, action="先核算税后收入"),
        ],
        disputes=[{"question": "口径", "status": "narrowed",
                   "missing_variable": "税后月收入"}],
        unknowns=["年薪口径未确认", "公积金额度未确认？"],
        gate_failures=[], partial=False,
        ledger_dict={"entries": [
            {"label": "月供", "value": "2.1万", "basis": "user_input"},
        ]},
        ledger_conflicts=["月供占收入比"],
    )
    cs = report.chair_summary
    assert "当前建议：" in cs
    assert "已确认：月供=2.1万" in cs
    assert "未确认" in cs and "年薪口径" in cs
    assert "推进条件" in cs and "税后月收入" in cs
    assert "下一步补充" in cs
    assert "数字冲突未对账：月供占收入比" in report.completion.limitations
