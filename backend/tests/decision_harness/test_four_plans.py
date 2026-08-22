"""四方案测试：待确认项分流 / 委托对账 / 立场多样性（提示词+steelman）/ 提速不回归。"""
import json

import pytest

from app.decision_harness.claim_router import classify_unknown, split_unknowns
from app.decision_harness.context_compiler import (
    build_expert_task_packet,
    compile_child_context,
    render_child_prompt,
)
from app.decision_harness.repository import (
    DecisionRunRepository,
    _clear_memory_for_tests,
)
from app.decision_harness.runtime import FakeAgentRuntime, RuntimeEvent
from app.decision_harness.schemas import DecisionModel, PlanSpec, SubAgentResult
from app.decision_harness.workflow import DecisionWorkflow, select_expert_tasks

DECISION = DecisionModel(
    question="要不要把家里那台开了 8 年的油车换成电车",
    options=["换电车", "继续开油车"],
    constraints=["预算 15 万内"],
)
PLAN = PlanSpec(
    plan_version=1, objective="评估换电车的经济性与家庭适用性",
    expert_tasks=["economist: 五年总持有成本对比", "sociologist: 家庭出行模式适配"],
    blocking_unknowns=[
        "家里那台油车的具体车型与车况",        # user
        "2026 年电车置换补贴政策",             # fact
        "父母的晕车情况与乘坐偏好",            # user
        "当前同级别电车的实际续航数据",        # fact
    ],
    created_from_state_version=1,
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


# ============ 方案一：待确认项分流 ============

def test_classify_unknown_user_vs_fact():
    assert classify_unknown("家里那台油车的具体车型与车况") == "user"
    assert classify_unknown("父母的真实意愿是什么") == "user"
    assert classify_unknown("我的存款和月收入水平") == "user"
    assert classify_unknown("2026 年电车置换补贴政策") == "fact"
    assert classify_unknown("当前同级别电车实际续航数据") == "fact"
    assert classify_unknown("竞业限制的法律后果") == "fact"


def test_split_unknowns_preserves_partition():
    user, fact = split_unknowns([t for t in PLAN.blocking_unknowns])
    assert len(user) == 2 and len(fact) == 2
    assert "家里那台油车的具体车型与车况" in user
    assert "2026 年电车置换补贴政策" in fact


async def test_research_routes_user_unknowns_to_questions():
    """用户型缺口不进搜索、写 decision_state.questions_for_user。"""
    from app.decision_harness.research_lead import NoopSearchProvider

    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="x", decision_state={}, budget={})
    await repo.update_run("run1", active_plan=PLAN.model_dump(mode="json"))
    wf = DecisionWorkflow(
        run_id="run1", decision=DECISION,
        budget=__import__("app.decision_harness.schemas", fromlist=["BudgetPolicy"]).BudgetPolicy(),
        runtime=FakeAgentRuntime(), repo=repo,
    )
    provider_calls = []

    class SpyProvider(NoopSearchProvider):
        async def search(self, query, *, max_results=5):
            provider_calls.append(query)
            return []

    events = [e async for e in wf.run_research(provider=SpyProvider())]

    # 搜索词不含用户型条目
    assert all("车型与车况" not in q and "晕车" not in q for q in provider_calls)
    # 用户型缺口转为问用户清单
    row = await repo.get_run("run1")
    questions = row["decision_state"].get("questions_for_user") or []
    assert any("车型" in q for q in questions)
    assert any("晕车" in q for q in questions)
    # 事实型缺口进了搜索（claim_verdict 事件存在，因为 Spy 无结果 → unverified）
    assert any(e["payload"].get("note") == "claim_verdict" for e in events)


# ============ 方案二：主席委托对账 ============

async def test_delegate_reconciliation_supersedes_and_merges():
    """同专家委托被 superseded；新专家且任务具体时并入执行。"""
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="x", decision_state={}, budget={})
    await repo.update_run("run1", active_plan=PLAN.model_dump(mode="json"))
    # 主席手动委托：economist（计划已覆盖→superseded）+ contrarian（计划没有→并入）
    await repo.upsert_agent_run(
        "run1:agent:del-1", decision_run_id="run1", agent_type="expert",
        expert_id="economist", task_id="del-1", status="pending_phase2",
        task_packet={"raw": {"task": " economists 重复任务但长度足够超过十字以触发合并判断"}},
    )
    await repo.upsert_agent_run(
        "run1:agent:del-2", decision_run_id="run1", agent_type="expert",
        expert_id="contrarian", task_id="del-2", status="pending_phase2",
        task_packet={"raw": {"task": "构造换电车决策的最强反方论证框架"}},
    )

    # contrarian 需要脚本才能完成；economist/sociologist 同理
    scripts = {
        "run1:expert:task-economist-1": [[_final({
            "status": "completed", "stance": "support", "judgment": "五年净省约 3.2 万，值得换",
            "confidence": 0.8, "action": "先试驾", "reversal_conditions": ["电池衰减超预期"],
        })]],
        "run1:expert:task-sociologist-2": [[_final({
            "status": "completed", "stance": "cautious", "judgment": "长途出行需家庭协作规划",
            "confidence": 0.7,
        })]],
        # 合并的 contrarian：task_id 沿用其委托行 task_id（del-2）
        "run1:expert:del-2": [[_final({
            "status": "completed", "stance": "oppose",
            "judgment": "问题被窄化为换或不换，该先问动力组合需求", "confidence": 0.8,
        })]],
    }
    # 补位到 3 位的 psychologist（select 会 pad）
    scripts.setdefault("run1:expert:task-psychologist-3", [[_final({
        "status": "completed", "stance": "cautious", "judgment": "动机混合，需区分",
        "confidence": 0.7,
    })]])

    wf = DecisionWorkflow(
        run_id="run1", decision=DECISION,
        budget=__import__("app.decision_harness.schemas", fromlist=["BudgetPolicy"]).BudgetPolicy(),
        runtime=FakeAgentRuntime(scripts={**scripts,
                                         "run1:main": [[_final({"ack": 1})]]}),
        repo=repo,
    )
    events = [e async for e in wf.run_experts()]

    rows = {r["expert_id"]: r for r in await repo.list_agent_runs("run1")}
    # economist 的委托行被 superseded（计划已有该专家）
    assert rows["economist"]["status"] in ("completed", "superseded_by_plan")
    del_row = rows.get("economist")
    # contrarian 委托并入执行：应有 completed 的结果行
    contrarian_rows = [r for r in rows.values() if r["expert_id"] == "contrarian"]
    assert any(r["status"] == "completed" for r in contrarian_rows), \
        [r["status"] for r in contrarian_rows]
    # 无 pending_phase2 残留（Gate 毒源清零）
    leftovers = [r for r in rows.values() if r["status"] == "pending_phase2"]
    assert leftovers == []
    notes = [e["payload"].get("detail") for e in events
             if e["event_type"] == "run_log" and e["payload"].get("note") == "expert_selection"]
    assert any(n and n.startswith(("superseded_delegate", "merged_delegate")) for n in notes)


# ============ 方案三：立场多样性 ============

def test_expert_prompt_requires_stance_diversity():
    packet = build_expert_task_packet(
        task_id="task-eco-1", expert_id="economist",
        objective="评估五年总持有成本差异与换车时机",
        decision=DECISION, plan=PLAN, context_version=2,
    )
    ctx = compile_child_context(packet, decision=DECISION, plan=PLAN)
    prompt = render_child_prompt(packet, ctx)
    # 立场引导在会话基础指令里（start_session 注入）
    from app.decision_harness.expert_loop import EXPERT_BASE_INSTRUCTIONS
    assert "support" in EXPERT_BASE_INSTRUCTIONS
    assert "满足什么条件即转为支持" in EXPERT_BASE_INSTRUCTIONS
    # 输出长度约束（方案四）
    assert "120" in EXPERT_BASE_INSTRUCTIONS
    assert "SubAgentResult" in prompt


async def test_steelman_dispute_synthesized_when_no_support():
    """零 support + ≥2 发言 → 合成支持方缺席检验争议，辩论点火。"""
    results = [
        SubAgentResult(task_id="t1", agent_id="economist", status="completed",
                       stance="cautious", judgment="再等等看充电设施", confidence=0.7),
        SubAgentResult(task_id="t2", agent_id="sociologist", status="completed",
                       stance="oppose", judgment="家庭模式不匹配", confidence=0.8),
    ]
    # 直接跑 run_debate：results 注入
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="x", decision_state={}, budget={})
    await repo.update_run("run1", active_plan=PLAN.model_dump(mode="json"))
    # 辩论需要 agent 行（线程恢复）
    for r in results:
        await repo.upsert_agent_run(
            f"run1:agent:{r.task_id}", decision_run_id="run1",
            agent_type="expert", expert_id=r.agent_id, task_id=r.task_id,
            status="completed", result=r.model_dump(mode="json"),
            runtime_thread_id=f"th-{r.agent_id}", finished=True,
        )
    debate_scripts = {
        "run1:expert:t1": [[_final({
            "dispute_id": "dispute-steelman", "agent_id": "economist",
            "position_before": "cautious", "position_after": "conditional_support",
            "changed": True, "reason": "若单位有充电桩则支持", "remaining_disagreement": "",
        })]],
        "run1:expert:t2": [[_final({
            "dispute_id": "dispute-steelman", "agent_id": "sociologist",
            "position_before": "oppose", "position_after": "oppose",
            "changed": False, "remaining_disagreement": "长途场景仍不支持",
        })]],
        "run1:main": [[_final({"ack": 1})]],
    }
    wf = DecisionWorkflow(
        run_id="run1", decision=DECISION,
        budget=__import__("app.decision_harness.schemas", fromlist=["BudgetPolicy"]).BudgetPolicy(),
        runtime=FakeAgentRuntime(scripts=debate_scripts), repo=repo,
    )
    events = [e async for e in wf.run_debate(results=results)]
    notes = [e["payload"].get("note") for e in events if e["event_type"] == "run_log"]
    assert "steelman_dispute_synthesized" in notes
    assert any(e["event_type"] == "dispute_opened" for e in events)
    row = await repo.get_run("run1")
    assert row["decision_state"]["disputes"], "合成争议应入库"


def test_steelman_not_synthesized_when_support_exists():
    """有 support 时走正常 structural 提取，不合成。"""
    from app.decision_harness.debate_loop import structural_disputes

    results = [
        SubAgentResult(task_id="t1", agent_id="economist", status="completed",
                       stance="support", judgment="净省钱明确", confidence=0.8),
        SubAgentResult(task_id="t2", agent_id="sociologist", status="completed",
                       stance="oppose", judgment="模式不匹配", confidence=0.7),
    ]
    assert len(structural_disputes(results)) == 1  # 正常对立


# ============ 方案四：提速不回归 ============

def test_scheduler_default_concurrency_raised():
    from app.decision_harness.scheduler import DEFAULT_MAX_CONCURRENCY

    assert DEFAULT_MAX_CONCURRENCY == 5


async def test_parallel_research_still_writes_all_evidence():
    from app.decision_harness.evidence_kernel import EvidenceKernel
    from app.decision_harness.research_lead import FakeSearchProvider, ResearchLead, SearchHit

    kernel = EvidenceKernel(run_id="runP1")
    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("runP1", runtime_mode="x", decision_state={}, budget={})
    provider = FakeSearchProvider(script={
        "政策": [SearchHit(url="https://www.gov.cn/p", title="官方政策")],
        "续航": [SearchHit(url="https://www.reuters.com/e", title="续航报道"),
                 SearchHit(url="https://www.gov.cn/e2", title="官方数据")],
    })
    lead = ResearchLead(run_id="runP1", kernel=kernel, repo=repo, provider=provider)
    from app.decision_harness.evidence_kernel import Claim

    events = [e async for e in lead.research_claims([
        Claim(claim_id="claim-1", text="2026 年电车置换补贴政策"),
        Claim(claim_id="claim-2", text="当前同级别电车实际续航数据"),
    ])]
    rows = await repo.list_evidence("runP1")
    assert len(rows) == 3  # 与旧串行路径相同的落库量
    assert lead.outcome.evidence_written == 3
    assert any(e["payload"].get("note") == "research_complete" for e in events)
