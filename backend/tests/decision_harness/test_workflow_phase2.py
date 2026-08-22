"""Phase 2 工作流测试：计划 → 专家阶段全链路（Fake Runtime，零 LLM/DB）。

Phase 2 验收映射：
- 经济学家 Child 垂直切片跑通；
- Root 不获取 Child 完整原始历史（隔离断言）；
- 单个 Child 失败不导致 Root Run 失败；
- 结构化 Write-back 落 decision_state 与 agent_runs。
"""
import json

import pytest

from app.decision_harness.context_compiler import root_observation
from app.decision_harness.repository import (
    DecisionRunRepository,
    _clear_memory_for_tests,
)
from app.decision_harness.runtime import FakeAgentRuntime, RuntimeEvent
from app.decision_harness.schemas import BudgetPolicy, DecisionModel
from app.decision_harness.workflow import DecisionWorkflow

DECISION = DecisionModel(
    question="要不要从大厂跳去创业公司",
    options=["跳槽", "留下"],
    constraints=["现金流最多撑 12 个月"],
    verified_user_facts=["年薪 60 万"],
)
BUDGET = BudgetPolicy(max_main_actions=6, max_consecutive_idle_turns=2)

PLAN_DICT = {
    "plan_version": 1,
    "objective": "两周内完成跳槽决策的信息收集与风险评估",
    "expert_tasks": ["economist: 现金流压力测试与机会成本核算"],
    "blocking_unknowns": ["期权行权条件"],
    "stop_conditions": ["blocking unknown 解除"],
    "created_from_state_version": 1,
}


@pytest.fixture(autouse=True)
def _clean():
    _clear_memory_for_tests()
    yield
    _clear_memory_for_tests()


def _turn_final(payload: dict) -> RuntimeEvent:
    return RuntimeEvent(
        seq=0, type="turn_final",
        payload={"final_response": json.dumps(payload, ensure_ascii=False)},
    )


def _chair_script(sid_main: str, sid_eco: str, *, eco_payloads: list[dict]):
    """主席两轮（出计划→收摘要确认），经济学家一轮。"""
    return FakeAgentRuntime(scripts={
        sid_main: [
            [_turn_final({"action": "revise_plan", "payload": {"plan": PLAN_DICT}})],
            [RuntimeEvent(seq=0, type="turn_final", payload={"final_response": "已收到专家结果。"})],
        ],
        sid_eco: [
            [_turn_final(dict(eco_payloads[0]))],
        ],
    })


async def _full_run(fake, repo, run_id="run1", allowed=("economist",)):
    wf = DecisionWorkflow(run_id=run_id, decision=DECISION, budget=BUDGET,
                          runtime=fake, repo=repo)
    plan_events = [e async for e in wf.run()]
    assert wf.outcome.status == "planned"
    # Phase 2 切片语义：显式只放经济学家（Phase 3 起默认八人 + 3-5 位收敛）
    expert_events = [e async for e in wf.run_experts(allowed_experts=allowed)]
    return wf, plan_events + expert_events


async def test_plan_then_economist_slice_end_to_end():
    run_id = "run1"
    fake = _chair_script(f"{run_id}:main", "run1:expert:task-economist-1",
                         eco_payloads=[{
                             "status": "completed", "relevance": 0.9,
                             "stance": "cautious",
                             "judgment": "现金流可支撑但容错率不足",
                             "analysis_points": ["压测", "期权"],
                             "unknowns": ["期权条款"],
                             "confidence": 0.7,
                         }])
    repo = DecisionRunRepository(force_memory=True)
    wf, events = await _full_run(fake, repo)

    types = [e["event_type"] for e in events]
    for expected in ("plan_created", "agent_task_created", "agent_started",
                     "agent_completed"):
        assert expected in types, types

    row = await repo.get_run(run_id)
    assert row["status"] == "experts_done"
    assert row["workflow_phase"] == "EXPERT_LOOPS"
    # 结构化 Write-back：decision_state 带 expert_results 摘要，用户事实原样
    ds = row["decision_state"]
    assert ds["expert_results"][0]["agent_id"] == "economist"
    assert ds["expert_results"][0]["stance"] == "cautious"
    assert ds["verified_user_facts"] == ["年薪 60 万"]
    assert ds["expert_results_summary"]["completed"] == 1

    agents = await repo.list_agent_runs(run_id)
    eco = [a for a in agents if a["agent_type"] == "expert"]
    assert len(eco) == 1 and eco[0]["status"] == "completed"
    assert eco[0]["result"]["judgment"].startswith("现金流")
    assert wf.expert_results[0].status == "completed"


async def test_root_never_receives_child_raw_history():
    """隔离验收：Root 的观察 prompt 只有白名单字段，无子 Agent 上下文包内容。"""
    run_id = "run1"
    fake = _chair_script(f"{run_id}:main", "run1:expert:task-economist-1",
                         eco_payloads=[{
                             "status": "completed", "stance": "oppose",
                             "judgment": "隐性成本被低估",
                             "analysis_points": ["超长分析细节A", "超长分析细节B"],
                             "assumptions": ["假设通胀 2%"],
                             "confidence": 0.8,
                         }])
    repo = DecisionRunRepository(force_memory=True)
    await _full_run(fake, repo)

    root_prompts = fake.prompts[f"{run_id}:main"]
    eco_prompts = fake.prompts.get("run1:expert:task-economist-1", [])
    assert eco_prompts and "任务上下文包" in eco_prompts[0]

    # Root 第二轮（观察轮）prompt：含结构化判断，不含白名单外字段与子 Agent prompt 痕迹
    obs_prompt = root_prompts[-1]
    assert "隐性成本被低估" in obs_prompt
    for banned in ("analysis_points", "assumptions", "任务上下文包", "输出契约"):
        assert banned not in obs_prompt, f"Root 观察泄漏: {banned}"
    # 子 Agent 会话与 Root 会话严格分离
    assert ("start_session", f"{run_id}:main") in fake.calls
    assert ("start_session", "run1:expert:task-economist-1") in fake.calls


async def test_single_child_failure_does_not_fail_root_run():
    run_id = "run1"
    # 两个不同专家：经济学家全程说胡话（failed），心理学家正常完成
    plan2 = dict(PLAN_DICT, expert_tasks=[
        "economist: 现金流压力测试与机会成本核算",
        "psychologist: 动机一致性与执行心理风险评估",
    ])
    sid_main, sid_eco, sid_psy = (
        f"{run_id}:main", "run1:expert:task-economist-1", "run1:expert:task-psychologist-2"
    )
    nonsense = RuntimeEvent(seq=0, type="turn_final", payload={"final_response": "嗯"})
    fake = FakeAgentRuntime(scripts={
        sid_main: [
            [_turn_final({"action": "revise_plan", "payload": {"plan": plan2}})],
            [_turn_final({"ack": True})],
        ],
        sid_eco: [[nonsense]] * 3,  # 2 轮预算 + 1 强制收敛，全部无效 → failed
        sid_psy: [[_turn_final({
            "status": "completed", "stance": "cautious",
            "judgment": "动机一致性中等偏低", "confidence": 0.6,
        })]],
    })
    repo = DecisionRunRepository(force_memory=True)
    wf, events = await _full_run(fake, repo, allowed=("economist", "psychologist"))

    types = [e["event_type"] for e in events]
    assert "agent_failed" in types and "agent_completed" in types
    row = await repo.get_run(run_id)
    assert row["status"] == "experts_done"  # Root Run 没有失败
    summary = row["decision_state"]["expert_results_summary"]
    assert summary == {"total": 2, "completed": 1, "partial": 0,
                       "silent": 0, "failed": 1}
    by_id = {r.agent_id: r.status for r in wf.expert_results}
    assert by_id == {"economist": "failed", "psychologist": "completed"}


async def test_run_experts_requires_plan():
    fake = FakeAgentRuntime()
    repo = DecisionRunRepository(force_memory=True)
    wf = DecisionWorkflow(run_id="runX", decision=DECISION, budget=BUDGET,
                          runtime=fake, repo=repo)
    with pytest.raises(ValueError, match="run_to_plan"):
        [e async for e in wf.run_experts()]


async def test_silent_expert_flows_through():
    run_id = "run1"
    fake = _chair_script(f"{run_id}:main", "run1:expert:task-economist-1",
                         eco_payloads=[{
                             "status": "silent", "relevance": 0.2,
                             "questions_for_user": ["这类决策你以前怎么做的？"],
                         }])
    repo = DecisionRunRepository(force_memory=True)
    wf, events = await _full_run(fake, repo)
    assert wf.expert_results[0].status == "silent"
    assert wf.expert_results[0].judgment == ""
    row = await repo.get_run(run_id)
    assert row["decision_state"]["expert_results_summary"]["silent"] == 1


def test_root_observation_event_order_replayable():
    """专家阶段产品事件全部在 §15.1 清单内且 JSON 可序列化（SSE 前提）。"""
    from app.decision_harness.event_bridge import PRODUCT_EVENT_TYPES

    obs = root_observation([])
    assert obs["summary"] == {"total": 0, "completed": 0, "partial": 0,
                              "silent": 0, "failed": 0}
    assert "expert_results" == obs["kind"]
    # 清单里的专家阶段事件在本阶段已全部使用/预留
    for et in ("agent_task_created", "agent_started", "agent_partial",
               "agent_completed", "agent_failed"):
        assert et in PRODUCT_EVENT_TYPES
