"""ExpertAgentRunner 测试：happy path、重试、强制收敛、partial、失败隔离。"""
import json

import pytest

from app.decision_harness.context_compiler import (
    build_expert_task_packet,
    compile_child_context,
)
from app.decision_harness.expert_loop import (
    ExpertAgentRunner,
    _extract_json_object,
    _parse_sub_agent_result,
)
from app.decision_harness.repository import (
    DecisionRunRepository,
    _clear_memory_for_tests,
)
from app.decision_harness.runtime import FakeAgentRuntime, RuntimeEvent
from app.decision_harness.schemas import DecisionModel, PlanSpec

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


@pytest.fixture(autouse=True)
def _clean():
    _clear_memory_for_tests()
    yield
    _clear_memory_for_tests()


def _packet():
    return build_expert_task_packet(
        task_id="task-eco-1", expert_id="economist",
        objective="评估 12 个月现金流断流风险与两选项的机会成本",
        decision=DECISION, plan=PLAN, context_version=2,
    )


def _final_event(payload: dict) -> RuntimeEvent:
    return RuntimeEvent(
        seq=0, type="turn_final",
        payload={"final_response": json.dumps(payload, ensure_ascii=False)},
    )


def _good_payload(**over):
    body = {
        "status": "completed", "relevance": 0.9, "stance": "cautious",
        "judgment": "现金流可支撑但容错率不足 3 个月",
        "analysis_points": ["月支出压测", "期权价值不可靠"],
        "unknowns": ["期权行权条款"],
        "action": "拿到书面期权条款前不辞职",
        "reversal_conditions": ["存款补充 50 万"],
        "confidence": 0.7,
    }
    body.update(over)
    return body


async def _collect(runner):
    return [e async for e in runner.run()]


async def test_happy_path_completed_with_writeback():
    packet = _packet()
    sid = f"run1:expert:{packet.task_id}"
    fake = FakeAgentRuntime(scripts={sid: [[_final_event(_good_payload())]]})
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="codex_harness",
                          decision_state={}, budget={})

    ctx = compile_child_context(packet, decision=DECISION, plan=PLAN)
    runner = ExpertAgentRunner(run_id="run1", packet=packet, context=ctx,
                               runtime=fake, repo=repo)
    events = await _collect(runner)

    types = [e["event_type"] for e in events]
    assert "agent_started" in types and "agent_completed" in types
    assert "agent_failed" not in types
    assert runner.outcome.result.status == "completed"
    assert runner.outcome.result.judgment.startswith("现金流")
    assert runner.outcome.turns_used == 1

    agents = await repo.list_agent_runs("run1")
    row = next(a for a in agents if a["id"] == runner.agent_run_id)
    assert row["status"] == "completed"
    assert row["result"]["stance"] == "cautious"
    assert row["task_packet"]["expert_id"] == "economist"
    # 会话已关（线程留在 Runtime 侧供 follow-up）
    assert ("close_session", sid) in fake.calls


async def test_invalid_then_valid_retries_within_budget():
    packet = _packet()
    sid = f"run1:expert:{packet.task_id}"
    garbage = RuntimeEvent(seq=0, type="turn_final", payload={"final_response": "我想先查资料"})
    fake = FakeAgentRuntime(scripts={sid: [
        [garbage],
        [_final_event(_good_payload())],
    ]})
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="codex_harness",
                          decision_state={}, budget={})
    ctx = compile_child_context(packet, decision=DECISION, plan=PLAN)
    runner = ExpertAgentRunner(run_id="run1", packet=packet, context=ctx,
                               runtime=fake, repo=repo)
    await _collect(runner)
    assert runner.outcome.result.status == "completed"
    assert runner.outcome.turns_used == 2
    # 第二轮 prompt 是继续提醒，不是重复整个上下文包
    assert "任务上下文包" not in fake.prompts[sid][1]


async def test_turn_cap_forced_finalize_partial():
    """轮次耗尽 → 强制收敛轮：child 自报 partial → partial 结果（安全有限结论）。"""
    packet = _packet()
    sid = f"run1:expert:{packet.task_id}"
    evasive = RuntimeEvent(seq=0, type="turn_final", payload={"final_response": "让我再想想"})
    finalize = _final_event(_good_payload(status="partial", confidence=0.4))
    fake = FakeAgentRuntime(scripts={sid: [
        [evasive], [evasive],  # child_turn_limit=2 用尽
        [finalize],            # 强制收敛轮
    ]})
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="codex_harness",
                          decision_state={}, budget={})
    ctx = compile_child_context(packet, decision=DECISION, plan=PLAN)
    runner = ExpertAgentRunner(run_id="run1", packet=packet, context=ctx,
                               runtime=fake, repo=repo, child_turn_limit=2)
    await _collect(runner)
    assert runner.outcome.result.status == "partial"
    assert runner.outcome.turns_used == 3
    assert "轮次预算已用完" in fake.prompts[sid][-1]


async def test_all_invalid_ends_failed_not_raise():
    packet = _packet()
    sid = f"run1:expert:{packet.task_id}"
    garbage = RuntimeEvent(seq=0, type="turn_final", payload={"final_response": "嗯……"})
    fake = FakeAgentRuntime(scripts={sid: [[garbage]] * 3})
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="codex_harness",
                          decision_state={}, budget={})
    ctx = compile_child_context(packet, decision=DECISION, plan=PLAN)
    runner = ExpertAgentRunner(run_id="run1", packet=packet, context=ctx,
                               runtime=fake, repo=repo, child_turn_limit=2)
    events = await _collect(runner)  # 不抛异常 = 失败隔离
    assert runner.outcome.result.status == "failed"
    assert "agent_failed" in [e["event_type"] for e in events]
    agents = await repo.list_agent_runs("run1")
    row = next(a for a in agents if a["id"] == runner.agent_run_id)
    assert row["status"] == "failed" and row["error_code"] == "expert_failed"


async def test_timeout_yields_partial_from_seen_text():
    packet = _packet()
    sid = f"run1:expert:{packet.task_id}"
    partial_text = RuntimeEvent(seq=0, type="item_completed", payload={
        "item": {"item_type": "AgentMessageThreadItem", "text": "初步看月固定支出约 3 万，缓冲不足"}
    })
    fake = FakeAgentRuntime(
        scripts={sid: [[partial_text, RuntimeEvent(seq=0, type="turn_final",
                                                   payload={"final_response": "继续"})]]},
        turn_delay_seconds=0.15,
    )
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="codex_harness",
                          decision_state={}, budget={})
    ctx = compile_child_context(packet, decision=DECISION, plan=PLAN)
    runner = ExpertAgentRunner(run_id="run1", packet=packet, context=ctx,
                               runtime=fake, repo=repo,
                               child_turn_limit=4, timeout_seconds=0.25)
    events = await _collect(runner)  # 超时不抛
    assert runner.outcome.result.status == "partial"
    assert runner.outcome.result.judgment.startswith("初步看")
    assert "超时" in runner.outcome.result.unknowns[0]


async def test_runtime_crash_isolated_as_failed():
    """Runtime 层抛任意异常 → failed 结果行，异常不穿透。"""
    class ExplodingRuntime(FakeAgentRuntime):
        async def start_session(self, session_id, *, instructions):
            raise RuntimeError("app-server process died")

    packet = _packet()
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="codex_harness",
                          decision_state={}, budget={})
    ctx = compile_child_context(packet, decision=DECISION, plan=PLAN)
    runner = ExpertAgentRunner(run_id="run1", packet=packet, context=ctx,
                               runtime=ExplodingRuntime(), repo=repo)
    events = await _collect(runner)
    assert runner.outcome.result.status == "failed"
    assert "expert_error" in runner.outcome.result.unknowns[0]
    agents = await repo.list_agent_runs("run1")
    assert agents and agents[0]["status"] == "failed"


def test_parse_helpers():
    assert _extract_json_object('前言 {"a": 1} 后记') == {"a": 1}
    assert _extract_json_object("```json\n{\"a\": 2}\n```") == {"a": 2}
    assert _extract_json_object("没有 json") is None
    packet = _packet()
    parsed = _parse_sub_agent_result(
        json.dumps({"judgment": "x" * 10, "status": "completed"}),
        packet,
    )
    assert parsed.task_id == packet.task_id  # 缺省字段由 packet 补齐
    assert parsed.agent_id == "economist"
    # silent 不得夹带观点（契约层拒绝）
    assert _parse_sub_agent_result(
        json.dumps({"status": "silent", "judgment": "我认为应该跳槽",
                    "analysis_points": ["理由"]}),
        packet,
    ) is None
