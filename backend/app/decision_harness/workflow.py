"""Decision Workflow 外壳 —— Phase 1 竖切（spec 2026-08-22 §6，Phase 1 范围）。

只覆盖 INTAKE → PLANNING_LOOP →（planned | waiting_user | failed）：
- 主 Agent 通过 AgentRuntime 跑 Agent Loop，每轮产出一个 MainAgentActionRequest；
- revise_plan 落 plan_versions（跨 worker 重启可恢复）；
- ask_user → WAITING_USER 退出；
- delegate_agent / request_research 只登记 agent_runs（Phase 2 才执行）；
- 事件经 event_bridge 转成产品事件，run() 流式 yield；
- 终态经 .outcome 读取（async generator 无法 return 值）。

Phase 1 明确不做（后续 Phase）：专家执行、研究、辩论、审计、终局。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.decision_harness.event_bridge import make_product_event, translate_runtime_event
from app.decision_harness.repository import DecisionRunRepository
from app.decision_harness.runtime import AgentRuntime
from app.decision_harness.schemas import (
    BudgetPolicy,
    DecisionModel,
    MainAgentAction,
    MainAgentActionRequest,
    PlanSpec,
    WorkflowPhase,
)

CHAIR_INSTRUCTIONS = """你是"邪杠会议室"的决策主席（Decision Chair）。

你的职责：把用户的决策问题转成一份可执行的研究计划，再按需调度专家。
每轮你只输出一个 JSON 对象（不要输出其他文本），结构为：
{"action": "<动作名>", "payload": {...}}

当前阶段允许的动作：
- revise_plan    payload.plan 是研究计划（PlanSpec JSON）。计划被接受后才会进入专家阶段。
- ask_user       payload.questions 是给用户的问题列表（缺少关键信息时用）。
- delegate_agent payload.task 是专家任务描述（Phase 2 生效）。
- request_research payload.topics 是需要核实的主题列表（Phase 2 生效）。

计划要求（PlanSpec）：objective 具体可验证；expert_tasks 只从八人库选
（economist/psychologist/historian/mathematician/strategist/legal_scholar/
sociologist/contrarian）；写清 blocking_unknowns 与 stop_conditions；
plan_version 从 1 开始递增。
"""

# Phase 1 主 Agent 允许的动作（spec §7.1 的子集，按阶段裁剪）
PHASE1_ALLOWED_ACTIONS = frozenset(
    {
        MainAgentAction.REVISE_PLAN,
        MainAgentAction.ASK_USER,
        MainAgentAction.DELEGATE_AGENT,
        MainAgentAction.REQUEST_RESEARCH,
    }
)

# Phase 2+ 才生效的动作 → 登记但不执行
DEFERRED_ACTIONS = {
    MainAgentAction.SEND_FOLLOWUP: "followup 需要活跃子 Agent（Phase 2）",
    MainAgentAction.START_DEBATE: "辩论需要专家结果（Phase 5）",
    MainAgentAction.RUN_EVIDENCE_AUDIT: "审计需要证据内核（Phase 6）",
    MainAgentAction.REQUEST_REVISION: "修订请求需要专家结果（Phase 2）",
    MainAgentAction.FINALIZE: "终局需要 Finalization Gate（Phase 6）",
}


@dataclass
class WorkflowOutcome:
    run_id: str
    status: str  # planned | waiting_user | failed
    actions_taken: int
    plan: PlanSpec | None = None
    error: str | None = None
    deferred: list[str] = field(default_factory=list)


def _chair_prompt(
    decision: DecisionModel, budget: BudgetPolicy, *, turn: int, resume: bool
) -> str:
    decision_json = json.dumps(decision.model_dump(mode="json"), ensure_ascii=False)
    budget_json = json.dumps(budget.model_dump(mode="json"), ensure_ascii=False)
    prefix = "（会话已从中断恢复，继续你未完成的计划工作）" if resume else ""
    return (
        f"{prefix}第 {turn + 1} 轮。决策状态：\n{decision_json}\n\n"
        f"预算策略：\n{budget_json}\n\n"
        "基于以上状态输出你的下一个动作 JSON（只输出 JSON）。"
    )


def _parse_action_request(final_response: str | None) -> MainAgentActionRequest:
    """final_response → MainAgentActionRequest。解析失败抛 ValueError。"""
    if not final_response or not final_response.strip():
        raise ValueError("主 Agent 空响应")
    text = final_response.strip()
    # 容错：剥掉 markdown 代码块围栏
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"主 Agent 响应不是 JSON: {text[:120]!r}")
    payload = json.loads(text[start : end + 1])
    return MainAgentActionRequest.model_validate(payload)


class DecisionWorkflow:
    """INTAKE → PLANNING_LOOP 的外壳。用法：

        wf = DecisionWorkflow(run_id=..., decision=..., budget=...,
                              runtime=..., repo=...)
        async for event in wf.run():  # 产品事件流（转 SSE / 落 parliament_events）
            ...
        outcome = wf.outcome  # 终态（run() 耗尽后有效）

    resume=True 时 run 行必须已存在（含 root_thread_id）——worker 重启恢复入口。
    """

    def __init__(
        self,
        *,
        run_id: str,
        decision: DecisionModel,
        budget: BudgetPolicy,
        runtime: AgentRuntime,
        repo: DecisionRunRepository,
        parliament_id: str | None = None,
        runtime_mode: str = "codex_harness",
        resume: bool = False,
    ):
        self.run_id = run_id
        self.decision = decision
        self.budget = budget
        self.runtime = runtime
        self.repo = repo
        self.parliament_id = parliament_id
        self.runtime_mode = runtime_mode
        self.resume = resume
        self.outcome: WorkflowOutcome | None = None

    def _finish(self, outcome: WorkflowOutcome) -> WorkflowOutcome:
        self.outcome = outcome
        return outcome

    async def run(self) -> AsyncIterator[dict[str, Any]]:
        run_id = self.run_id
        session_id = f"{run_id}:main"
        repo, runtime = self.repo, self.runtime

        created = await repo.create_run(
            run_id,
            parliament_id=self.parliament_id,
            runtime_mode=self.runtime_mode,
            decision_state=self.decision.model_dump(mode="json"),
            budget=self.budget.model_dump(mode="json"),
        )
        if not created and not self.resume:
            raise ValueError(
                f"decision_run {run_id} 已存在（如需恢复请传 resume=True）"
            )

        if self.resume:
            row = await repo.get_run(run_id)
            if row is None:
                raise ValueError(f"resume 模式但 decision_run {run_id} 不存在")
            await runtime.resume_session(
                session_id, runtime_thread_id=row.get("root_thread_id")
            )
            yield make_product_event(run_id, "runtime_recovered", {"session_id": session_id})
        else:
            await runtime.start_session(session_id, instructions=CHAIR_INSTRUCTIONS)
            # CodexAgentRuntime 暴露线程 id 供重启恢复；Fake 等实现没有 → None
            thread_id_getter = getattr(runtime, "runtime_thread_id", None)
            if callable(thread_id_getter):
                await repo.update_run(
                    run_id, root_thread_id=thread_id_getter(session_id)
                )
            await repo.upsert_agent_run(
                f"{run_id}:main-run",
                decision_run_id=run_id,
                agent_type="main",
                runtime_provider=type(runtime).__name__,
                status="running",
                started=True,
            )

        await repo.update_run(run_id, workflow_phase=WorkflowPhase.PLANNING_LOOP.value)
        yield make_product_event(
            run_id, "run_log", {"phase": WorkflowPhase.PLANNING_LOOP.value}
        )

        latest_plan: PlanSpec | None = None
        deferred: list[str] = []
        idle_turns = 0
        actions_taken = 0

        if self.resume:
            # 状态再水化：active_plan 是重启前的最新计划（DB 是 source of truth）
            row = await repo.get_run(run_id)
            if row and row.get("active_plan"):
                latest_plan = PlanSpec.model_validate(row["active_plan"])

        for turn in range(self.budget.max_main_actions):
            prompt = _chair_prompt(
                self.decision, self.budget,
                turn=turn, resume=self.resume and turn == 0,
            )
            final_response: str | None = None
            async for event in runtime.start_turn(session_id, prompt):
                translated = translate_runtime_event(run_id, event)
                if translated is not None:
                    yield translated
                if event.type == "turn_final":
                    final_response = event.payload.get("final_response")
                elif event.type == "turn_interrupted":
                    self._finish(WorkflowOutcome(
                        run_id=run_id, status="failed", actions_taken=actions_taken,
                        plan=latest_plan, error="turn_interrupted", deferred=deferred,
                    ))
                    yield make_product_event(
                        run_id, "run_log", {"note": "turn_interrupted"}
                    )
                    return
            actions_taken += 1

            try:
                request = _parse_action_request(final_response)
            except (ValueError, json.JSONDecodeError) as exc:
                idle_turns += 1
                yield make_product_event(
                    run_id, "run_log",
                    {"note": "invalid_chair_response", "detail": str(exc)[:200]},
                )
                if idle_turns >= self.budget.max_consecutive_idle_turns:
                    # 已有计划 → 空转视为"计划完成"而非失败（主席交回控制权）
                    if latest_plan is not None:
                        await repo.update_run(run_id, status="planned")
                        yield make_product_event(
                            run_id, "run_log",
                            {"note": "planning_complete",
                             "plan_version": latest_plan.plan_version,
                             "reason": "chair_idle_with_plan"},
                        )
                        self._finish(WorkflowOutcome(
                            run_id=run_id, status="planned",
                            actions_taken=actions_taken, plan=latest_plan,
                            deferred=deferred,
                        ))
                        return
                    await repo.update_run(
                        run_id, status="failed",
                        error=f"chair_idle:{str(exc)[:400]}",
                    )
                    yield make_product_event(
                        run_id, "agent_failed",
                        {"agent": "main", "message": "连续无效响应"},
                    )
                    self._finish(WorkflowOutcome(
                        run_id=run_id, status="failed", actions_taken=actions_taken,
                        error=f"chair_idle:{str(exc)[:200]}", deferred=deferred,
                    ))
                    return
                continue

            action = request.action
            if action not in PHASE1_ALLOWED_ACTIONS:
                if action in DEFERRED_ACTIONS:
                    deferred.append(action.value)
                    yield make_product_event(
                        run_id, "run_log",
                        {"note": "action_deferred", "action": action.value,
                         "reason": DEFERRED_ACTIONS[action]},
                    )
                else:
                    idle_turns += 1
                continue

            if action == MainAgentAction.REVISE_PLAN:
                try:
                    plan = PlanSpec.model_validate(request.payload.get("plan") or {})
                    version = plan.plan_version
                    if latest_plan is not None and version <= latest_plan.plan_version:
                        version = latest_plan.plan_version + 1
                        plan = plan.model_copy(update={"plan_version": version})
                except Exception as exc:
                    idle_turns += 1
                    yield make_product_event(
                        run_id, "run_log",
                        {"note": "invalid_plan", "detail": str(exc)[:200]},
                    )
                    continue
                revised = latest_plan is not None
                latest_plan = plan
                await repo.append_plan_version(
                    run_id, plan.plan_version, plan.model_dump(mode="json"),
                    "chair_revision" if revised else "initial",
                )
                await repo.update_run(
                    run_id, active_plan=plan.model_dump(mode="json"),
                    state_version=plan.plan_version,
                )
                yield make_product_event(
                    run_id,
                    "plan_revised" if revised else "plan_created",
                    {
                        "plan_version": plan.plan_version,
                        "objective": plan.objective,
                        "expert_tasks": plan.expert_tasks,
                        "research_tasks": plan.research_tasks,
                        "blocking_unknowns": plan.blocking_unknowns,
                    },
                )
                continue

            if action == MainAgentAction.ASK_USER:
                questions = request.payload.get("questions") or []
                await repo.update_run(run_id, status="waiting_user")
                yield make_product_event(
                    run_id, "user_input_requested", {"questions": questions}
                )
                self._finish(WorkflowOutcome(
                    run_id=run_id, status="waiting_user", actions_taken=actions_taken,
                    plan=latest_plan, deferred=deferred,
                ))
                return

            if action == MainAgentAction.DELEGATE_AGENT:
                agent_run_id = f"{run_id}:agent:{actions_taken}"
                await repo.upsert_agent_run(
                    agent_run_id,
                    decision_run_id=run_id,
                    agent_type="expert",
                    task_id=str(request.payload.get("task_id") or agent_run_id),
                    status="pending_phase2",
                    task_packet={"raw": request.payload},
                )
                yield make_product_event(
                    run_id, "agent_task_created",
                    {"agent_run_id": agent_run_id, "note": "Phase 2 才执行"},
                )
                continue

            if action == MainAgentAction.REQUEST_RESEARCH:
                yield make_product_event(
                    run_id, "run_log",
                    {"note": "research_deferred",
                     "topics": request.payload.get("topics", [])[:5]},
                )
                continue

        # 预算走完
        if latest_plan is not None:
            await repo.update_run(run_id, status="planned")
            yield make_product_event(
                run_id, "run_log",
                {"note": "planning_complete", "plan_version": latest_plan.plan_version},
            )
            self._finish(WorkflowOutcome(
                run_id=run_id, status="planned", actions_taken=actions_taken,
                plan=latest_plan, deferred=deferred,
            ))
        else:
            await repo.update_run(
                run_id, status="failed", error="budget_exhausted_without_plan"
            )
            yield make_product_event(
                run_id, "agent_failed",
                {"agent": "main", "message": "预算耗尽仍未产出计划"},
            )
            self._finish(WorkflowOutcome(
                run_id=run_id, status="failed", actions_taken=actions_taken,
                error="budget_exhausted_without_plan", deferred=deferred,
            ))
