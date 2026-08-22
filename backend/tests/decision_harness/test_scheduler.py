"""Phase 3 测试：Scheduler 并发/取消、选择规则、相关度门、越界检查、follow-up。"""
import json

import pytest

from app.decision_harness.context_compiler import (
    build_expert_task_packet,
    compile_child_context,
)
from app.decision_harness.event_bridge import PRODUCT_EVENT_TYPES
from app.decision_harness.expert_loop import (
    ExpertAgentRunner,
    check_role_boundary,
    enforce_relevance_gate,
    send_followup,
)
from app.decision_harness.repository import (
    DecisionRunRepository,
    _clear_memory_for_tests,
)
from app.decision_harness.runtime import FakeAgentRuntime, RuntimeEvent
from app.decision_harness.scheduler import ExpertJob, ExpertScheduler
from app.decision_harness.schemas import (
    DecisionModel,
    EXPERT_IDS,
    PlanSpec,
    SubAgentResult,
)
from app.decision_harness.workflow import DEFAULT_EXPERT_TASKS, select_expert_tasks

DECISION = DecisionModel(
    question="要不要从大厂跳去创业公司",
    options=["跳槽", "留下"],
    constraints=["现金流最多撑 12 个月"],
)
PLAN = PlanSpec(
    plan_version=1,
    objective="两周内完成跳槽决策的信息收集与风险评估",
    expert_tasks=["economist: 现金流压力测试"],
    created_from_state_version=1,
)
ALL = tuple(sorted(EXPERT_IDS))


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


def _job(run_id: str, task_id: str, expert_id: str) -> ExpertJob:
    packet = build_expert_task_packet(
        task_id=task_id, expert_id=expert_id,
        objective=f"{DEFAULT_EXPERT_TASKS[expert_id]}（{task_id}）",
        decision=DECISION, plan=PLAN, context_version=2,
    )
    return ExpertJob(
        packet=packet,
        context=compile_child_context(packet, decision=DECISION, plan=PLAN),
    )


def _good(agent: str, judgment: str, **over) -> dict:
    body = {
        "status": "completed", "relevance": 0.85, "stance": "cautious",
        "judgment": judgment, "confidence": 0.7, "analysis_points": ["要点"],
    }
    body.update(over)
    return body


# ============ 选择规则 ============

def test_selection_dedupe_and_cap():
    plan = PlanSpec(plan_version=1, objective="x" * 20, expert_tasks=[
        "economist: 现金流压力测试", "economist: 重复任务应被去重",
        "psychologist: 动机评估", "contrarian: 反方论证",
        "historian: 历史先例",
    ], created_from_state_version=1)
    tasks, notes = select_expert_tasks(plan, allowed_experts=ALL)
    ids = [t[0] for t in tasks]
    assert len(ids) == len(set(ids)) == 4  # 去重后 4 位（≤5 不截断）
    assert "duplicate_task_dropped:economist" in notes


def test_selection_pads_to_three_and_respects_user_choice():
    one = PlanSpec(plan_version=1, objective="x" * 20,
                   expert_tasks=["historian: 历史先例"],
                   created_from_state_version=1)
    tasks, notes = select_expert_tasks(one, allowed_experts=ALL)
    assert [t[0] for t in tasks] == ["historian", "economist", "psychologist"]
    assert any(n.startswith("padded:") for n in notes)

    tasks2, _ = select_expert_tasks(
        one, allowed_experts=ALL, user_experts=("historian", "legal_scholar")
    )
    assert {t[0] for t in tasks2} == {"historian", "legal_scholar"}


def test_all_eight_profiles_have_defaults():
    assert set(DEFAULT_EXPERT_TASKS) == set(EXPERT_IDS)
    for obj in DEFAULT_EXPERT_TASKS.values():
        assert len(obj) >= 10  # ExpertTaskPacket 具体性下限


# ============ Scheduler 并发与隔离 ============

async def test_scheduler_concurrency_cap_and_completion():
    run_id = "run1"
    scripts = {}
    jobs = []
    for i, eid in enumerate(("economist", "psychologist", "strategist"), 1):
        tid = f"task-{eid}-{i}"
        scripts[f"{run_id}:expert:{tid}"] = [[_final(_good(eid, f"{eid} 的判断"))]]
        jobs.append(_job(run_id, tid, eid))
    fake = FakeAgentRuntime(scripts=scripts, turn_delay_seconds=0.05)
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run(run_id, runtime_mode="codex_harness",
                          decision_state={}, budget={})

    sched = ExpertScheduler(run_id=run_id, runtime=fake, repo=repo, max_concurrency=2)
    events = [e async for e in sched.run_all(jobs)]

    assert sched.observed_concurrency <= 2  # 并发上限（spec §16.2）
    assert len(sched.outcomes) == 3
    assert all(o.result.status == "completed" for o in sched.outcomes)
    started = [e for e in events if e["event_type"] == "agent_started"]
    assert len(started) == 3
    assert all(e["event_type"] in PRODUCT_EVENT_TYPES for e in events)
    agents = await repo.list_agent_runs(run_id)
    assert len([a for a in agents if a["agent_type"] == "expert"]) == 3


async def test_scheduler_parallel_failure_isolation():
    run_id = "run1"
    nonsense = RuntimeEvent(seq=0, type="turn_final", payload={"final_response": "嗯"})
    scripts = {
        f"{run_id}:expert:task-economist-1": [[nonsense]] * 3,
        f"{run_id}:expert:task-psychologist-2": [[_final(_good("psychologist", "动机清晰"))]],
    }
    fake = FakeAgentRuntime(scripts=scripts)
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run(run_id, runtime_mode="codex_harness",
                          decision_state={}, budget={})
    jobs = [_job(run_id, "task-economist-1", "economist"),
            _job(run_id, "task-psychologist-2", "psychologist")]

    sched = ExpertScheduler(run_id=run_id, runtime=fake, repo=repo, child_turn_limit=2)
    events = [e async for e in sched.run_all(jobs)]

    by_id = {o.packet.expert_id: o.result.status for o in sched.outcomes}
    assert by_id == {"economist": "failed", "psychologist": "completed"}
    assert "agent_failed" in [e["event_type"] for e in events]


async def test_scheduler_cancel_mid_flight():
    run_id = "run1"
    tid = "task-economist-1"
    sid = f"{run_id}:expert:{tid}"
    slow = RuntimeEvent(seq=0, type="item_completed", payload={
        "item": {"item_type": "AgentMessageThreadItem", "text": "正在算……"}
    })
    fake = FakeAgentRuntime(scripts={sid: [[slow, _final(_good("e", "x" * 10))]]},
                            turn_delay_seconds=0.3)
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run(run_id, runtime_mode="codex_harness",
                          decision_state={}, budget={})

    sched = ExpertScheduler(run_id=run_id, runtime=fake, repo=repo)
    collected = []
    async for event in sched.run_all([_job(run_id, tid, "economist")]):
        collected.append(event)
        if event["event_type"] == "agent_started":
            await sched.cancel()  # 用户/调度器取消

    outcome = sched.outcomes[0]
    assert outcome.result.status == "failed"
    assert "cancelled" in (outcome.result.unknowns[0] if outcome.result.unknowns else "")
    # 取消后不再消耗后续轮
    assert fake.turn_count(sid) <= 1


# ============ 相关度门与越界检查 ============

def test_relevance_gate_converts_low_relevance_to_silent():
    result = SubAgentResult(
        task_id="t", agent_id="economist", status="completed",
        relevance=0.1, judgment="其实我没什么可说的",
        analysis_points=["强行凑的观点"],
        questions_for_user=["q1", "q2"], confidence=0.9,
    )
    gated = enforce_relevance_gate(result)
    assert gated.status == "silent"
    assert gated.judgment == "" and gated.analysis_points == []
    assert gated.questions_for_user == ["q1"]  # silent 至多一个问题
    # 非 completed 不动
    partial = result.model_copy(update={"status": "partial"})
    assert enforce_relevance_gate(partial).status == "partial"
    # 高相关度不动
    ok = result.model_copy(update={"relevance": 0.8})
    assert enforce_relevance_gate(ok).status == "completed"


def test_boundary_check_flags_url_without_evidence():
    r = SubAgentResult(task_id="t", agent_id="economist",
                       judgment="详见 http://example.com/report")
    assert check_role_boundary(r) == "judgment_cites_url_without_evidence_refs"
    r2 = SubAgentResult(task_id="t", agent_id="economist",
                        judgment="详见 http://example.com/report",
                        evidence_refs=["ev-1"])
    assert check_role_boundary(r2) is None


async def test_runner_applies_gate_and_boundary_events():
    run_id = "run1"
    tid = "task-economist-1"
    sid = f"{run_id}:expert:{tid}"
    payload = _good("economist", "低相关但夹带 http://x 的观点",
                    relevance=0.05)
    fake = FakeAgentRuntime(scripts={sid: [[_final(payload)]]})
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run(run_id, runtime_mode="codex_harness",
                          decision_state={}, budget={})
    packet = _job(run_id, tid, "economist").packet
    ctx = compile_child_context(packet, decision=DECISION, plan=PLAN)
    runner = ExpertAgentRunner(run_id=run_id, packet=packet, context=ctx,
                               runtime=fake, repo=repo)
    events = [e async for e in runner.run()]

    result = runner.outcome.result
    assert result.status == "silent"           # 相关度门生效
    assert result.judgment == ""               # 不生成伪观点
    assert result.completion.boundary_checked is True
    # 越界事件只作记录，不改变 silent 事实
    assert any(e["event_type"] == "run_log" and e["payload"].get("note") == "role_boundary"
               for e in events)


# ============ Follow-up ============

async def test_send_followup_updates_result_via_thread_resume():
    run_id = "run1"
    tid = "task-economist-1"
    sid = f"{run_id}:expert:{tid}"
    updated = _good("economist", "补充计算后：容错率约 4 个月", stance="oppose",
                    confidence=0.8)
    fake = FakeAgentRuntime(scripts={sid: [
        [_final(_good("economist", "初步：容错率不足"))],   # 首轮
        [_final(updated)],                                  # follow-up 轮
    ]})
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run(run_id, runtime_mode="codex_harness",
                          decision_state={}, budget={})
    job = _job(run_id, tid, "economist")
    runner = ExpertAgentRunner(run_id=run_id, packet=job.packet,
                               context=job.context, runtime=fake, repo=repo)
    [e async for e in runner.run()]
    assert runner.outcome.result.stance == "cautious"

    row = next(a for a in await repo.list_agent_runs(run_id)
               if a["id"] == runner.agent_run_id)
    assert row["runtime_thread_id"] == f"fake-thread:{sid}"  # 线程句柄已持久化

    new_result, ok, events = await send_followup(
        run_id=run_id, agent_run_row=row,
        questions=["按书面期权条款重算一次"], runtime=fake, repo=repo,
    )
    assert ok is True
    assert new_result.stance == "oppose"
    assert "补充计算后" in new_result.judgment
    assert any(e["event_type"] == "agent_completed" and e["payload"].get("followup")
               for e in events)
    row2 = next(a for a in await repo.list_agent_runs(run_id)
                if a["id"] == runner.agent_run_id)
    assert row2["result"]["judgment"].startswith("补充计算后")


async def test_send_followup_invalid_keeps_old_result():
    run_id = "run1"
    tid = "task-economist-1"
    sid = f"{run_id}:expert:{tid}"
    garbage = RuntimeEvent(seq=0, type="turn_final", payload={"final_response": "不想改"})
    fake = FakeAgentRuntime(scripts={sid: [
        [_final(_good("economist", "初步判断"))],
        [garbage],
    ]})
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run(run_id, runtime_mode="codex_harness",
                          decision_state={}, budget={})
    job = _job(run_id, tid, "economist")
    runner = ExpertAgentRunner(run_id=run_id, packet=job.packet,
                               context=job.context, runtime=fake, repo=repo)
    [e async for e in runner.run()]
    old = runner.outcome.result

    row = next(a for a in await repo.list_agent_runs(run_id)
               if a["id"] == runner.agent_run_id)
    new_result, ok, events = await send_followup(
        run_id=run_id, agent_run_row=row, questions=["q"],
        runtime=fake, repo=repo,
    )
    assert ok is False
    assert new_result.judgment == old.judgment  # 旧结果保留
    assert any(e["payload"].get("note") == "followup_invalid_response" for e in events)
