"""Phase 1 工作流竖切测试：run_to_plan 全路径（Fake Runtime 驱动，零 LLM/DB）。

覆盖 spec Phase 1 验收：
- 计划落 plan_versions，仓库重建（模拟 worker 重启）后仍在；
- resume=True 恢复 Codex 线程并续跑；
- 事件顺序可重放（seq 单调、产品事件类型合法）。
"""
import json

import pytest

from app.decision_harness.event_bridge import PRODUCT_EVENT_TYPES
from app.decision_harness.repository import (
    DecisionRunRepository,
    _clear_memory_for_tests,
)
from app.decision_harness.runtime import FakeAgentRuntime, RuntimeEvent
from app.decision_harness.schemas import BudgetPolicy, DecisionModel
from app.decision_harness.workflow import DecisionWorkflow


@pytest.fixture(autouse=True)
def _clean_memory():
    _clear_memory_for_tests()
    yield
    _clear_memory_for_tests()


DECISION = DecisionModel(
    question="要不要从大厂跳去创业公司",
    options=["跳槽", "留下"],
    constraints=["现金流最多撑 12 个月"],
)
BUDGET = BudgetPolicy(max_main_actions=6, max_consecutive_idle_turns=2)


def _turn_final_event(action: str, payload: dict | None = None) -> RuntimeEvent:
    body = {"action": action, "payload": payload or {}}
    return RuntimeEvent(seq=0, type="turn_final",
                        payload={"final_response": json.dumps(body, ensure_ascii=False)})


def _partial_event(text: str) -> RuntimeEvent:
    return RuntimeEvent(seq=0, type="item_completed", payload={
        "item": {"item_type": "AgentMessageThreadItem", "text": text}
    })


def _plan_payload(version: int = 1) -> dict:
    return {
        "plan_version": version,
        "objective": "在两周内完成跳槽决策的信息收集与风险评估",
        "expert_tasks": ["economist: 现金流压力测试", "psychologist: 风险偏好评估"],
        "blocking_unknowns": ["期权行权条件"],
        "stop_conditions": ["两个 blocking unknown 被解除"],
        "created_from_state_version": 1,
    }


async def _collect(wf: DecisionWorkflow) -> list[dict]:
    return [event async for event in wf.run()]


async def test_happy_path_plan_then_ask_user():
    sid = "run1:main"
    fake = FakeAgentRuntime(scripts={
        sid: [
            [_partial_event("我先看一下决策状态"), _turn_final_event("revise_plan", {"plan": _plan_payload(1)})],
            [_turn_final_event("ask_user", {"questions": ["期权行权条件能拿到书面条款吗？"]})],
        ],
    })
    repo = DecisionRunRepository(force_memory=True)
    wf = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BUDGET,
                          runtime=fake, repo=repo)
    events = await _collect(wf)

    types = [e["event_type"] for e in events]
    assert "plan_created" in types
    assert "agent_partial" in types
    assert "user_input_requested" in types

    assert wf.outcome.status == "waiting_user"
    assert wf.outcome.plan.plan_version == 1

    row = await repo.get_run("run1")
    assert row["status"] == "waiting_user"
    assert row["active_plan"]["objective"].startswith("在两周内完成")
    versions = await repo.list_plan_versions("run1")
    assert [v["version"] for v in versions] == [1]

    # 用户问题透传
    asked = next(e for e in events if e["event_type"] == "user_input_requested")
    assert asked["payload"]["questions"][0].startswith("期权行权")


async def test_duplicate_run_id_requires_resume():
    fake = FakeAgentRuntime(scripts={"run1:main": [[_turn_final_event("ask_user")]]})
    repo = DecisionRunRepository(force_memory=True)
    wf1 = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BUDGET,
                           runtime=fake, repo=repo)
    await _collect(wf1)

    wf2 = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BUDGET,
                           runtime=fake, repo=repo)
    with pytest.raises(ValueError, match="resume=True"):
        await _collect(wf2)


async def test_resume_after_restart_continues_plan():
    """验收核心：run 行 + 计划在仓库重建后仍可恢复，续跑产出 v2。"""
    sid = "run1:main"
    fake1 = FakeAgentRuntime(scripts={
        sid: [[_turn_final_event("revise_plan", {"plan": _plan_payload(1)})],
              [_turn_final_event("ask_user", {"questions": ["q"]})]],
    })
    repo1 = DecisionRunRepository(force_memory=True)
    wf1 = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BUDGET,
                           runtime=fake1, repo=repo1)
    events1 = await _collect(wf1)
    assert wf1.outcome.status == "waiting_user"

    # 模拟 worker 重启：全新 runtime + 全新仓库实例
    fake2 = FakeAgentRuntime(scripts={
        sid: [[_turn_final_event("revise_plan", {"plan": _plan_payload(2)})]],
    })
    await fake2.start_session(sid, instructions="warm")  # Fake 语义：先存在才能 resume
    repo2 = DecisionRunRepository(force_memory=True)
    wf2 = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BUDGET,
                           runtime=fake2, repo=repo2, resume=True)
    events2 = await _collect(wf2)

    types2 = [e["event_type"] for e in events2]
    assert "runtime_recovered" == types2[0]
    assert "plan_revised" in types2
    assert wf2.outcome.status == "planned"
    assert wf2.outcome.plan.plan_version == 2

    state = fake2.session_state(sid)
    assert state["resumed"] is True
    assert state["resumed_thread_id"] == (await repo2.get_run("run1"))["root_thread_id"]

    versions = await repo2.list_plan_versions("run1")
    assert [v["version"] for v in versions] == [1, 2]  # 历史版本保留


async def test_budget_exhausted_without_plan_fails():
    sid = "run1:main"
    fake = FakeAgentRuntime(scripts={
        sid: [
            [RuntimeEvent(seq=0, type="turn_final", payload={"final_response": "我不知道"})],
            [RuntimeEvent(seq=0, type="turn_final", payload={"final_response": "还是不知道"})],
        ],
    })
    repo = DecisionRunRepository(force_memory=True)
    wf = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BUDGET,
                          runtime=fake, repo=repo)
    events = await _collect(wf)

    assert wf.outcome.status == "failed"
    assert "chair_idle" in wf.outcome.error
    types = [e["event_type"] for e in events]
    assert "agent_failed" in types
    row = await repo.get_run("run1")
    assert row["status"] == "failed"
    assert "chair_idle" in row["error"]


async def test_deferred_actions_logged_not_executed():
    sid = "run1:main"
    fake = FakeAgentRuntime(scripts={
        sid: [
            # 计划前的委托被守卫忽略并提醒
            [_turn_final_event("delegate_agent", {"task": {"expert_id": "economist"}})],
            [_turn_final_event("revise_plan", {"plan": _plan_payload(1)})],
            [_turn_final_event("start_debate", {"dispute_id": "d1"})],
            # 计划后的委托才登记 pending_phase2
            [_turn_final_event("delegate_agent", {"task": {"expert_id": "economist"}})],
            [_turn_final_event("ask_user", {"questions": ["补充信息？"]})],
        ],
    })
    repo = DecisionRunRepository(force_memory=True)
    wf = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BUDGET,
                          runtime=fake, repo=repo)
    events = await _collect(wf)

    assert wf.outcome.status == "waiting_user"
    notes = [e["payload"].get("note") for e in events if e["event_type"] == "run_log"]
    assert "delegate_before_plan_ignored" in notes
    deferred = [e["payload"].get("action") for e in events
                if e["event_type"] == "run_log" and e["payload"].get("note") == "action_deferred"]
    assert deferred == ["start_debate"]
    tasks = [e for e in events if e["event_type"] == "agent_task_created"]
    assert len(tasks) == 1
    agents = await repo.list_agent_runs("run1")
    expert_rows = [a for a in agents if a["agent_type"] == "expert"]
    assert len(expert_rows) == 1
    assert expert_rows[0]["status"] == "pending_phase2"


async def test_plan_payload_shapes_normalized():
    """真跑实测：GLM 把计划平铺在 payload、expert_tasks 用对象数组。"""
    sid = "run1:main"
    flat_payload = {
        "plan_version": 1,
        "objective": "两周内完成跳槽决策的信息收集与风险评估",
        "expert_tasks": [
            {"expert": "psychologist", "task": "动机一致性与逃避模式评估"},
            {"expert": "contrarian", "objective": "构造最强反方论证"},
        ],
        "research_tasks": [{"topic": "行业薪酬基准"}],
        "blocking_unknowns": ["期权行权条件"],
        "created_from_state_version": 1,
    }
    fake = FakeAgentRuntime(scripts={
        sid: [
            [_turn_final_event("revise_plan", flat_payload)],  # 平铺形态
            [_turn_final_event("ask_user", {"questions": ["q"]})],
        ],
    })
    repo = DecisionRunRepository(force_memory=True)
    wf = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BUDGET,
                          runtime=fake, repo=repo)
    events = await _collect(wf)
    assert wf.outcome.status == "waiting_user"
    assert wf.outcome.plan is not None
    assert wf.outcome.plan.expert_tasks[0] == "psychologist: 动机一致性与逃避模式评估"
    assert wf.outcome.plan.expert_tasks[1] == "contrarian: 构造最强反方论证"
    assert wf.outcome.plan.research_tasks == ["行业薪酬基准"]
    assert any(e["event_type"] == "plan_created" for e in events)

async def test_chair_action_aliases_normalized():
    """真跑实测：主席自造 synthesize 动作 → 映射为 finalize（登记 deferred 不算 idle）。"""
    import json as _json
    sid = "run1:main"
    fake = FakeAgentRuntime(scripts={sid: [
        [RuntimeEvent(seq=0, type="turn_final", payload={"final_response":
            _json.dumps({"action": "synthesize", "payload": {"report": "..."}})})],
        [_turn_final_event("revise_plan", {"plan": _plan_payload(1)})],
        [_turn_final_event("ask_user", {"questions": ["q"]})],
    ]})
    repo = DecisionRunRepository(force_memory=True)
    wf = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BUDGET,
                          runtime=fake, repo=repo)
    events = await _collect(wf)
    assert wf.outcome.status == "waiting_user"
    assert "finalize" in wf.outcome.deferred  # 别名生效且未判 idle


async def test_plan_real_shape_from_rollout():
    """真跑 rollout 原始形态：payload.plan 嵌套 + 缺 created_from_state_version
    + 超长 objective + 对象条目 —— 归一化后必须可接受。"""
    sid = "run1:main"
    real_shape = {
        "action": "revise_plan",
        "payload": {"plan": {
            "plan_version": 1,
            "objective": "构建多维度分析框架，使决策者掌握经济成本、心理摩擦、"
                         "照护可行性、社会法律因素及配偶底线后做出有依据的选择。"
                         "可验证标准：产出两方案在6个维度的对比矩阵+关键拐点条件+阶段性建议。" * 2,
            "expert_tasks": [
                {"expert": "economist", "task": "量化比较两方案 3-5 年总成本并给敏感性分析"},
                {"expert": "psychologist", "task": "分析代际摩擦风险与试运行期设计"},
                {"expert": "sociologist", "task": "社区支持网络与养老观念差异分析"},
            ],
            "blocking_unknowns": ["配偶底线", "父母意愿", "预算上限"],
        }},
    }
    import json as _json
    fake = FakeAgentRuntime(scripts={sid: [
        [RuntimeEvent(seq=0, type="turn_final",
                      payload={"final_response": _json.dumps(real_shape, ensure_ascii=False)})],
        [_turn_final_event("ask_user", {"questions": ["q"]})],
    ]})
    repo = DecisionRunRepository(force_memory=True)
    wf = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BUDGET,
                          runtime=fake, repo=repo)
    events = await _collect(wf)
    assert wf.outcome.plan is not None
    assert len(wf.outcome.plan.objective) <= 300
    assert wf.outcome.plan.created_from_state_version == 1
    assert wf.outcome.plan.expert_tasks[0].startswith("economist: ")
    assert any(e["event_type"] == "plan_created" for e in events)



async def test_event_stream_replayable_and_schema_safe():
    """事件顺序可重放：无重复 seq 缺口、全部在 §15.1 清单内。"""
    sid = "run1:main"
    fake = FakeAgentRuntime(scripts={
        sid: [
            [_partial_event("p1"), _turn_final_event("revise_plan", {"plan": _plan_payload(1)})],
            [_partial_event("p2"), _turn_final_event("revise_plan", {"plan": _plan_payload(2)})],
        ],
    })
    repo = DecisionRunRepository(force_memory=True)
    wf = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BUDGET,
                          runtime=fake, repo=repo)
    events = await _collect(wf)
    assert wf.outcome.status == "planned"

    assert all(e["event_type"] in PRODUCT_EVENT_TYPES for e in events)
    # runtime 来源的事件 seq 单调（workflow 自发事件 seq=0，由 parliament_events
    # 落库时重新分配全局 seq——见 event_store.append_event）
    seqs = [e["seq"] for e in events if e["seq"] > 0]
    assert seqs == sorted(seqs)
    # JSON 可序列化（SSE / parliament_events 落库前提）
    json.dumps(events, ensure_ascii=False)


async def test_plan_version_monotonic_when_chair_repeats_version():
    sid = "run1:main"
    fake = FakeAgentRuntime(scripts={
        sid: [
            [_turn_final_event("revise_plan", {"plan": _plan_payload(3)})],  # 主席重复报 3
            [_turn_final_event("revise_plan", {"plan": _plan_payload(3)})],  # 再报 3 → 强制 4
        ],
    })
    repo = DecisionRunRepository(force_memory=True)
    wf = DecisionWorkflow(run_id="run1", decision=DECISION, budget=BUDGET,
                          runtime=fake, repo=repo)
    await _collect(wf)
    versions = await repo.list_plan_versions("run1")
    assert [v["version"] for v in versions] == [3, 4]
    assert wf.outcome.plan.plan_version == 4
