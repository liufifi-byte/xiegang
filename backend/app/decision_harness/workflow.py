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

from app.decision_harness.context_compiler import (
    build_expert_task_packet,
    compile_child_context,
    root_observation,
)
from app.decision_harness.event_bridge import make_product_event, translate_runtime_event
from app.decision_harness.expert_loop import ExpertAgentRunner
from app.decision_harness.repository import DecisionRunRepository
from app.decision_harness.runtime import AgentRuntime, AgentRuntimeError
from app.decision_harness.scheduler import ExpertJob, ExpertScheduler
from app.decision_harness.schemas import (
    ConflictType,
    DisputeIssue,
    BudgetPolicy,
    DecisionModel,
    EXPERT_IDS,
    MainAgentAction,
    MainAgentActionRequest,
    MAX_EXPERTS,
    MIN_EXPERTS,
    PlanSpec,
    RunExitStatus,
    SubAgentResult,
    WorkflowPhase,
)

CHAIR_INSTRUCTIONS = """你是"邪杠会议室"的决策主席（Decision Chair）。

你的职责：把用户的决策问题转成一份可执行的研究计划，再按需调度专家。
每轮你只输出一个 JSON 对象（不要输出其他文本），结构为：
{"action": "<动作名>", "payload": {...}}

当前阶段允许的动作（严格按此顺序）：
1. revise_plan —— 你的第一个动作必须是它。payload.plan 是研究计划
   （PlanSpec JSON）。没有计划，一切都不会开始。
2. ask_user —— 仅当缺关键信息时；用户不可用时会被告知基于现有信息继续。
3. delegate_agent / request_research —— 只在计划已给出之后才有意义；
   计划未出时这两个动作会被忽略并收到系统提醒。
重要：不要跳过 revise_plan 直接委托专家——委托不是计划。
动作名是封闭集合，只允许：revise_plan / ask_user / delegate_agent /
request_research。不存在 synthesize、finalize、summarize 等动作——
综合与最终报告由系统在专家阶段结束后自动执行，你不需要也不能触发。

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


# Phase 3：八人库全员接入（spec §5.3）；默认任务只作计划缺项时的补位
DEFAULT_EXPERT_TASKS = {
    "economist": "评估各选项在未来 12 个月的现金流压力、隐性成本与机会成本",
    "psychologist": "评估该决策背后的动机、逃避模式与执行阶段的心理风险",
    "historian": "寻找同类决策的历史先例，归纳其长期结局的规律与教训",
    "mathematician": "建立量化比较框架，识别决定性变量与各选项的盈亏阈值",
    "strategist": "分析各方博弈格局、谈判筹码与行动的时机窗口",
    "legal_scholar": "识别该决策涉及的法律义务、合同约束与合规风险",
    "sociologist": "评估该决策对家庭角色、社会关系与身份认同的影响",
    "contrarian": "构造当前倾向的最强反方论证，检验其脆弱点与适用边界",
}

# 补位顺序（计划专家不足 3 位时，按此顺序补到 MIN_EXPERTS）
_PAD_ORDER = ("economist", "psychologist", "contrarian", "strategist",
              "mathematician", "historian", "legal_scholar", "sociologist")


def _normalize_plan_body(payload: dict) -> dict:
    """兼容主席输出的两种计划形态（真跑实测）：
    ① {"action":"revise_plan","payload":{"plan":{...}}}（文档形态）
    ② {"action":"revise_plan","payload":{...计划字段直接平铺}}（GLM 实际形态）
    以及 expert_tasks 的字符串 / 对象两种条目。
    """
    body = payload.get("plan") if isinstance(payload.get("plan"), dict) else dict(payload)

    def _task_str(entry):
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict):
            expert = entry.get("expert") or entry.get("expert_id") or entry.get("agent") or ""
            task = entry.get("task") or entry.get("objective") or entry.get("goal") or ""
            if expert and task:
                return f"{expert}: {task}"
            return str(task or "") or None
        return None

    tasks = [_task_str(e) for e in body.get("expert_tasks") or []]
    body["expert_tasks"] = [t for t in tasks if t][:5]  # PlanSpec 上限
    research = [
        (r.get("topic") if isinstance(r, dict) else r)
        for r in body.get("research_tasks") or []
    ]
    body["research_tasks"] = [str(r) for r in research if r][:20]

    # 内部簿记字段模型不会给（真跑实测）：补默认
    body.setdefault("created_from_state_version", 1)
    if not isinstance(body.get("created_from_state_version"), int) or body["created_from_state_version"] < 1:
        body["created_from_state_version"] = 1
    if not isinstance(body.get("plan_version"), int) or body["plan_version"] < 1:
        body["plan_version"] = 1
    # objective 防御：去空白 + 截到契约上限
    objective = str(body.get("objective") or "").strip()
    body["objective"] = objective[:300]
    def _item_text(x):
        """未知项可能是 {id, description} 对象（真跑实测）——取人话字段，
        不把原始 dict 字符串泄漏进用户可见清单。"""
        if isinstance(x, dict):
            return str(x.get("description") or x.get("question")
                       or x.get("label") or x.get("id") or "")[:200]
        return str(x)[:200]

    for list_field in ("blocking_unknowns", "non_blocking_unknowns", "stop_conditions",
                       "expected_conflicts"):
        raw = body.get(list_field) or []
        body[list_field] = [_item_text(x) for x in raw if _item_text(x)][:10]
    return body


def select_expert_tasks(
    plan: PlanSpec,
    *,
    allowed_experts: tuple[str, ...],
    user_experts: tuple[str, ...] | None = None,
) -> tuple[list[tuple[str, str]], list[str]]:
    """计划任务 → 最终调度清单（spec §5.3：3-5 位、每专家一任务）。

    规则：解析 "expert_id: objective" → 过滤白名单与用户选择 →
    按专家去重（重复任务不产生重复发言）→ 超过 5 位截断、不足 3 位
    按 _PAD_ORDER 补位。返回 (清单, 调整说明)。
    """
    notes: list[str] = []
    pool = dict(DEFAULT_EXPERT_TASKS)
    if user_experts is not None:
        allowed_experts = tuple(e for e in allowed_experts if e in user_experts)

    picked: dict[str, str] = {}
    for entry in plan.expert_tasks:
        expert_id, _, objective = entry.partition(":")
        expert_id = expert_id.strip()
        objective = objective.strip()
        if expert_id not in allowed_experts:
            continue
        if expert_id in picked:
            notes.append(f"duplicate_task_dropped:{expert_id}")
            continue
        if len(objective) < 10:
            objective = pool.get(expert_id, objective) or objective
        picked[expert_id] = objective

    if len(picked) > MAX_EXPERTS:
        dropped = list(picked.keys())[MAX_EXPERTS:]
        notes.append("trimmed:" + ",".join(dropped))
        picked = dict(list(picked.items())[:MAX_EXPERTS])
    if len(picked) < MIN_EXPERTS:
        for expert_id in _PAD_ORDER:
            if len(picked) >= MIN_EXPERTS:
                break
            if expert_id in picked or expert_id not in allowed_experts:
                continue
            picked[expert_id] = pool[expert_id]
            notes.append(f"padded:{expert_id}")

    return list(picked.items()), notes


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
    # 真跑实测：主席会自造动作词（synthesize 等）。别名统一收口到契约动作。
    action_alias = {
        "synthesize": "finalize", "synthesis": "finalize",
        "synthesize_report": "finalize", "summarize": "finalize",
        "final_report": "finalize", "plan": "revise_plan",
        "research": "request_research", "delegate": "delegate_agent",
    }
    if isinstance(payload, dict):
        raw_action = str(payload.get("action") or "").strip().lower()
        if raw_action in action_alias:
            payload["action"] = action_alias[raw_action]
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
        pre_created: bool = False,
        auto_continue_without_user: bool = False,
    ):
        self.run_id = run_id
        self.decision = decision
        self.budget = budget
        self.runtime = runtime
        self.repo = repo
        self.parliament_id = parliament_id
        self.runtime_mode = runtime_mode
        self.resume = resume
        # 行已由调用方预建（shadow runner 等）：容忍 create_run 幂等返回 False
        self.pre_created = pre_created
        # 影子模式：ask_user 不终止，注入"用户不可用"观察后继续
        self.auto_continue_without_user = auto_continue_without_user
        self._pending_observation: str | None = None
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
        if not created and not self.resume and not self.pre_created:
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
            if self._pending_observation:
                prompt += f"\n\n{self._pending_observation}"
                self._pending_observation = None
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
                    plan = PlanSpec.model_validate(_normalize_plan_body(request.payload))
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
                yield make_product_event(
                    run_id, "user_input_requested", {"questions": questions}
                )
                if self.auto_continue_without_user:
                    # 影子模式无真实用户：记录问题，注入观察让主席继续
                    self._pending_observation = (
                        "【用户暂不可用（影子模式）】以上问题无法得到回答。"
                        "请基于现有信息继续：把无法确认的点写进计划的 "
                        "blocking_unknowns，并给出当前信息下的最优计划"
                        "（revise_plan）。"
                    )
                    continue
                await repo.update_run(run_id, status="waiting_user")
                self._finish(WorkflowOutcome(
                    run_id=run_id, status="waiting_user", actions_taken=actions_taken,
                    plan=latest_plan, deferred=deferred,
                ))
                return

            if action == MainAgentAction.DELEGATE_AGENT:
                # 主席可能用自造的 agents 数组格式（真跑实测）——归一成单条
                agents_list = request.payload.get("agents")
                if isinstance(agents_list, list) and agents_list:
                    first = agents_list[0] if isinstance(agents_list[0], dict) else {}
                    request = request.model_copy(update={
                        "payload": {"task_id": first.get("task_id"), "task": first}
                    })
                    if len(agents_list) > 1:
                        yield make_product_event(run_id, "run_log", {
                            "note": "delegate_agents_array_normalized",
                            "extra": len(agents_list) - 1,
                        })
                if latest_plan is None:
                    # 计划未出：委托无效——提醒主席先出计划（不登记 pending 行）
                    self._pending_observation = (
                        "【系统】尚未收到 revise_plan，该委托暂不生效。"
                        "请立即输出 revise_plan（PlanSpec JSON），"
                        "专家任务写在计划的 expert_tasks 里。"
                    )
                    yield make_product_event(
                        run_id, "run_log",
                        {"note": "delegate_before_plan_ignored"},
                    )
                    continue
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
                if latest_plan is None:
                    self._pending_observation = (
                        "【系统】请先输出 revise_plan——研究主题应写在计划的 "
                        "research_tasks / blocking_unknowns 里。"
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
            # §16：硬预算不得直接标记失败——进入 PARTIAL_FINALIZED
            await repo.update_run(
                run_id, status="partial_finalized",
                exit_status=RunExitStatus.PARTIAL_FINALIZED.value,
                error=None,
                finalization_state={"budget_exhausted": True,
                                    "stage": "planning"},
            )
            yield make_product_event(
                run_id, "partial_finalized",
                {"message": "计划预算耗尽，未形成研究计划",
                 "remaining_uncertainties": self.decision.unknowns[:5]},
            )
            self._finish(WorkflowOutcome(
                run_id=run_id, status="partial_finalized",
                actions_taken=actions_taken,
                error="budget_exhausted_without_plan", deferred=deferred,
            ))

    # ============================================================
    # Phase 2：专家阶段（DELEGATION → EXPERT_LOOPS → Root 结构化观察）
    # ============================================================

    async def run_experts(
        self,
        *,
        allowed_experts: tuple[str, ...] | None = None,
        user_experts: tuple[str, ...] | None = None,
        max_concurrency: int = 3,
    ) -> AsyncIterator[dict[str, Any]]:
        """按计划跑专家子 Agent（Phase 3：八人库 + 并发调度）。

        前置：run_to_plan 已产出计划（本实例 outcome 或 DB active_plan）。
        选择：select_expert_tasks 过滤白名单/用户选择，去重，收敛到 3-5 位。
        并发：ExpertScheduler（默认同会议最多 3 个活跃 Child，spec §16.2）。
        失败隔离：单个子 Agent 失败/超时只产生 failed 结果行，不影响其他。
        终态：run status="experts_done"（辩论/审计/终局属 Phase 5/6）。
        """
        run_id = self.run_id
        repo, runtime = self.repo, self.runtime
        if allowed_experts is None:
            allowed_experts = tuple(sorted(EXPERT_IDS))

        plan = self.outcome.plan if (self.outcome and self.outcome.plan) else None
        if plan is None:
            row = await repo.get_run(run_id)
            if row and row.get("active_plan"):
                plan = PlanSpec.model_validate(row["active_plan"])
        if plan is None:
            raise ValueError("run_experts 需要先完成计划（先跑 run_to_plan）")

        await repo.update_run(run_id, workflow_phase=WorkflowPhase.DELEGATION.value)
        yield make_product_event(run_id, "run_log", {"phase": WorkflowPhase.DELEGATION.value})

        tasks, notes = select_expert_tasks(
            plan, allowed_experts=allowed_experts, user_experts=user_experts
        )
        # 主席委托对账（彩排复盘：手动委托的 pending 行毒化终审 Gate）：
        # 同专家已被计划覆盖 → 标记 superseded；计划没覆盖且有余量 → 并入执行
        planned_ids = {eid for eid, _ in tasks}
        # 合并委托时沿用其原 task_id（脚本/线程/agent 行对得上，账目单一）
        merged_task_ids: dict[str, str] = {}
        pending_rows = [
            r for r in await repo.list_agent_runs(run_id)
            if r.get("agent_type") == "expert" and r.get("status") == "pending_phase2"
        ]
        for row in pending_rows:
            eid = row.get("expert_id") or ""
            raw = (row.get("task_packet") or {}).get("raw") or {}
            objective = ""
            if isinstance(raw, dict):
                t = raw.get("task")
                if isinstance(t, dict):
                    objective = str(t.get("task") or t.get("objective") or "")[:300]
                else:
                    objective = str(t or raw.get("objective") or "")[:300]
            if eid in planned_ids or eid not in allowed_experts \
                    or eid not in EXPERT_IDS or len(objective) < 10 \
                    or len(tasks) >= MAX_EXPERTS:
                await repo.upsert_agent_run(
                    row["id"], decision_run_id=run_id, agent_type="expert",
                    expert_id=eid, status="superseded_by_plan",
                )
                notes.append(f"superseded_delegate:{eid}")
            else:
                tasks.append((eid, objective))
                planned_ids.add(eid)
                merged_task_ids[eid] = str(row.get("task_id") or f"task-{eid}-merged")
                notes.append(f"merged_delegate:{eid}")
        for note in notes:
            yield make_product_event(run_id, "run_log", {"note": "expert_selection", "detail": note})
        if not tasks:
            raise ValueError(
                "专家选择结果为空（allowed_experts/user_experts 与计划无交集）"
            )
        # 计划之后的第一次上下文编译：版本 = 计划版本 + 1
        context_version = plan.plan_version + 1

        jobs: list[ExpertJob] = []
        for i, (expert_id, objective) in enumerate(tasks, start=1):
            packet = build_expert_task_packet(
                task_id=merged_task_ids.get(expert_id) or f"task-{expert_id}-{i}",
                expert_id=expert_id,
                objective=objective,
                decision=self.decision,
                plan=plan,
                context_version=context_version,
            )
            jobs.append(ExpertJob(
                packet=packet,
                context=compile_child_context(
                    packet, decision=self.decision, plan=plan,
                    evidence=(self.evidence_kernel.usable_summary()
                              if getattr(self, "evidence_kernel", None) else None),
                ),
            ))
            yield make_product_event(
                run_id, "agent_task_created",
                {"agent_id": expert_id, "task_id": packet.task_id,
                 "objective": objective},
            )

        await repo.update_run(run_id, workflow_phase=WorkflowPhase.EXPERT_LOOPS.value)
        yield make_product_event(
            run_id, "run_log", {"phase": WorkflowPhase.EXPERT_LOOPS.value}
        )

        scheduler = ExpertScheduler(
            run_id=run_id, runtime=runtime, repo=repo,
            max_concurrency=max_concurrency,
        )
        self.expert_scheduler = scheduler  # 供 cancel 集成（API 层）
        async for event in scheduler.run_all(jobs):
            yield event
        results = [o.result for o in scheduler.outcomes]

        # 统一数字账本（评审机制一）：专家报数汇账，冲突升级为对账争议
        from app.decision_harness.number_ledger import collect_from_results

        ledger = collect_from_results(results, round_no=1)
        row_l = await repo.get_run(run_id)
        state_l = dict(row_l.get("decision_state") or {}) if row_l else {}
        state_l["number_ledger"] = ledger.to_dict()
        await repo.update_run(run_id, decision_state=state_l)
        self.number_ledger = ledger
        for conflict in ledger.unresolved_conflicts()[:3]:
            yield make_product_event(run_id, "run_log", {
                "note": "number_conflict",
                "label": conflict.label,
                "values": [e.to_dict() for e in conflict.values[:3]],
            })

        # Root 观察：只喂结构化摘要，绝不吸收 Child 原始历史（spec §13.3）
        observation = root_observation(results)
        root_session = f"{run_id}:main"
        try:
            async for event in runtime.start_turn(
                root_session,
                "专家阶段结束。以下为结构化结果摘要（非原始记录）：\n"
                + json.dumps(observation, ensure_ascii=False, indent=2)
                + "\n请简短确认已收到（一句话即可，Phase 5 将进入争议提取）。",
            ):
                translated = translate_runtime_event(run_id, event)
                if translated is not None:
                    yield translated
        except AgentRuntimeError:
            yield make_product_event(
                run_id, "run_log",
                {"note": "root_session_absent", "hint": "resume 后再跑专家阶段"},
            )

        # 结构化结果写回 decision_state（verified_user_facts 不动）
        row = await repo.get_run(run_id)
        state = dict(row.get("decision_state") or {}) if row else {}
        state["expert_results"] = observation["results"]
        state["expert_results_summary"] = observation["summary"]
        state["expert_results_context_version"] = context_version
        await repo.update_run(run_id, decision_state=state, status="experts_done")

        self.expert_results = results
        yield make_product_event(
            run_id, "run_log",
            {"note": "experts_complete", **observation["summary"]},
        )

    # ============================================================
    # Phase 4：Research Lead（claim 驱动研究 → Evidence Kernel）
    # ============================================================

    async def run_research(
        self,
        *,
        provider: Any | None = None,
        claims: list[Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """对计划的 blocking_unknowns/research_tasks 跑 claim 驱动研究。

        provider 缺省 NoopSearchProvider（触发降级路径：claims 保持
        unverified，Run 不受影响）。证据写 Evidence Kernel + evidence_records，
        随后 run_experts 编译上下文时自动携带可用证据摘要。
        """
        from app.decision_harness.evidence_kernel import Claim, EvidenceKernel
        from app.decision_harness.research_lead import (
            NoopSearchProvider,
            ResearchLead,
        )

        run_id = self.run_id
        plan = self.outcome.plan if (self.outcome and self.outcome.plan) else None
        if plan is None:
            row = await self.repo.get_run(run_id)
            if row and row.get("active_plan"):
                plan = PlanSpec.model_validate(row["active_plan"])
        if plan is None:
            raise ValueError("run_research 需要先完成计划（先跑 run_to_plan）")

        if claims is None:
            # 方案一：待确认项分流（claim_router）——用户型转"问用户"，
            # 事实型才进搜索。彩排复盘：用户型问题上网搜=导购软文+永远 PARTIAL。
            from app.decision_harness.claim_router import split_unknowns

            user_unknowns, fact_unknowns = split_unknowns(
                [t for t in plan.blocking_unknowns if t and len(t) >= 2]
            )
            claims = [
                Claim(claim_id=f"claim-{i}", text=text, domain="general")
                for i, text in enumerate(fact_unknowns + list(plan.research_tasks), start=1)
                if text and len(text) >= 6
            ]
            if user_unknowns:
                row0 = await self.repo.get_run(run_id)
                state0 = dict(row0.get("decision_state") or {}) if row0 else {}
                state0["questions_for_user"] = user_unknowns[:8]
                await self.repo.update_run(run_id, decision_state=state0)
        kernel = getattr(self, "evidence_kernel", None) or EvidenceKernel(run_id=run_id)
        self.evidence_kernel = kernel

        lead = ResearchLead(
            run_id=run_id, kernel=kernel, repo=self.repo,
            provider=provider or NoopSearchProvider(),
            max_rounds=self.budget.max_search_rounds_per_gap,
        )
        async for event in lead.research_claims(claims):
            yield event

    # ============================================================
    # Phase 5：Debate Loop（争议提取 → 定向辩论）
    # ============================================================

    async def run_debate(
        self,
        *,
        results: list[SubAgentResult] | None = None,
        max_rounds: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """从专家结果提取实质分歧并跑定向辩论（spec §9）。

        只围绕立场对立（support vs oppose）的实质分歧开辩论；
        每争议最多 max_debate_rounds_per_dispute 轮（默认 2）；
        允许保留分歧。终态 status="debated"（审计/终局属 Phase 6）。
        """
        from app.decision_harness.debate_loop import (
            DebateRunner,
            structural_disputes,
        )

        run_id = self.run_id
        if results is None:
            results = getattr(self, "expert_results", None) or []
        if not results:
            rows = await self.repo.list_agent_runs(run_id)
            results = [
                SubAgentResult.model_validate(r["result"])
                for r in rows
                if r.get("agent_type") == "expert" and r.get("result")
            ]
        if not results:
            raise ValueError("run_debate 需要专家结果（先跑 run_experts）")

        await self.repo.update_run(run_id, workflow_phase=WorkflowPhase.DISPUTE_EXTRACTION.value)
        yield make_product_event(run_id, "run_log", {"phase": WorkflowPhase.DISPUTE_EXTRACTION.value})

        # 统一追问关口（评审问题 6）：辩论前聚合全部"结论反转变量"问一次，
        # 而不是散落在各专家末尾。生产模式 → WAITING_USER；影子模式 → 记录并继续。
        consolidated_questions: list[str] = []
        seen_q: set[str] = set()
        row_q = await self.repo.get_run(run_id)
        for q in ((row_q.get("decision_state") or {}).get("questions_for_user") or []):
            if q not in seen_q:
                seen_q.add(q)
                consolidated_questions.append(str(q)[:120])
        for r in results:
            for q in r.questions_for_user:
                if q not in seen_q:
                    seen_q.add(q)
                    consolidated_questions.append(str(q)[:120])
        consolidated_questions = consolidated_questions[:8]
        if consolidated_questions:
            state_q = dict((row_q.get("decision_state") or {})) if row_q else {}
            state_q["questions_for_user"] = consolidated_questions
            await self.repo.update_run(run_id, decision_state=state_q)
            yield make_product_event(run_id, "user_input_requested",
                                     {"questions": consolidated_questions})
            if not getattr(self, "auto_continue_without_user", False):
                await self.repo.update_run(run_id, status="waiting_user")
                self.pending_user_questions = consolidated_questions
                return

        # 数字冲突争议（评审机制一）：同标签不同值 → 强制一轮对账
        ledger_obj = getattr(self, "number_ledger", None)
        if ledger_obj is None and row_q:
            from app.decision_harness.number_ledger import NumberLedger
            ledger_obj = NumberLedger.from_dict(
                (row_q.get("decision_state") or {}).get("number_ledger"))
            self.number_ledger = ledger_obj
        number_disputes: list[DisputeIssue] = []
        if ledger_obj is not None:
            for i, conflict in enumerate(ledger_obj.unresolved_conflicts()[:2], start=1):
                participants = [e.source_agent for e in conflict.values[:2]
                                if e.source_agent in EXPERT_IDS]
                if len(participants) < 2:
                    participants = list(dict.fromkeys(
                        [e.source_agent for e in conflict.values] + ["mathematician", "economist"]
                    ))[:2]
                number_disputes.append(DisputeIssue(
                    dispute_id=f"dispute-number-{i}",
                    question=(f"数字对账：{conflict.label} 出现多个值（"
                              + " vs ".join(e.raw_value for e in conflict.values[:3])
                              + "）。哪一个是正确口径？请对账后给出统一值与依据。"),
                    importance=0.7,
                    conflict_type=ConflictType.FACT_CONFLICT,
                    claims=[e.to_dict() for e in conflict.values[:3]],
                    participants=participants,
                    status="open", round=0,
                ))
            for d in number_disputes:
                yield make_product_event(run_id, "run_log", {
                    "note": "number_reconciliation_opened", "label": d.dispute_id})

        disputes = structural_disputes(results)
        if not disputes:
            # 立场多样性兜底（彩排复盘：全员 cautious、辩论永不点火）：
            # 存在非支持立场但零 support 时，合成一次"支持方缺席检验"——
            # 让最自信的谨慎者 steelman 支持方，强制制造一次对立供辩论检验。
            speaking = [r for r in results
                        if r.status in ("completed", "partial") and r.judgment.strip()]
            supports = [r for r in speaking if r.stance == "support"]
            non_supports = [r for r in speaking if r.stance != "support"]
            if not supports and len(non_supports) >= 2:
                steeler = max(non_supports, key=lambda r: r.confidence)
                challenger = max(
                    (r for r in non_supports if r.agent_id != steeler.agent_id),
                    key=lambda r: r.confidence, default=None,
                )
                if challenger is not None:
                    disputes = [DisputeIssue(
                        dispute_id="dispute-steelman",
                        question=(
                            "支持方缺席检验：假如该决策的最强支持论证成立，"
                            "需要满足哪些条件？这些条件现实可达吗？"
                        ),
                        importance=0.5,
                        conflict_type=ConflictType.ASSUMPTION_CONFLICT,
                        claims=[
                            {"agent_id": steeler.agent_id, "stance": steeler.stance,
                             "claim": steeler.judgment[:200]},
                        ],
                        participants=[steeler.agent_id, challenger.agent_id],
                        missing_variable="支持方成立所需的前提条件",
                        status="open", round=0,
                    )]
                    yield make_product_event(run_id, "run_log",
                                             {"note": "steelman_dispute_synthesized"})
        if not disputes:
            await self.repo.update_run(run_id, workflow_phase=WorkflowPhase.DEBATE_LOOP.value)
            yield make_product_event(run_id, "run_log", {"note": "no_material_disputes"})
            await self.repo.update_run(run_id, status="debated")
            self.disputes = []
            return

        # expert_id → agent_runs 行（拿 runtime_thread_id 恢复线程）
        agent_rows = {
            r["expert_id"]: r
            for r in await self.repo.list_agent_runs(run_id)
            if r.get("agent_type") == "expert" and r.get("expert_id")
        }

        await self.repo.update_run(run_id, workflow_phase=WorkflowPhase.DEBATE_LOOP.value)
        yield make_product_event(run_id, "run_log", {"phase": WorkflowPhase.DEBATE_LOOP.value})

        disputes = number_disputes + disputes

        budget_rounds = max_rounds or self.budget.max_debate_rounds_per_dispute
        ledger_snapshot = (self.number_ledger.snapshot_for_prompt()
                           if getattr(self, "number_ledger", None) else "")
        outcomes = []
        for dispute in disputes:
            # steelman 压力测试与数字对账：最多 1 轮（评审机制二）
            rounds_for_dispute = (
                1 if dispute.dispute_id in ("dispute-steelman",)
                or dispute.dispute_id.startswith("dispute-number-")
                else budget_rounds
            )
            runner = DebateRunner(
                run_id=run_id, dispute=dispute, agent_rows=agent_rows,
                runtime=self.runtime, repo=self.repo,
                max_rounds=rounds_for_dispute,
                ledger_snapshot=ledger_snapshot,
            )
            async for event in runner.run():
                yield event
            if runner.outcome is not None:
                outcomes.append(runner.outcome)
                if (dispute.dispute_id.startswith("dispute-number-")
                        and dispute.status == "resolved"
                        and getattr(self, "number_ledger", None)):
                    self.number_ledger.mark_resolved(dispute.question[:20])
                    row_m = await self.repo.get_run(run_id)
                    state_m = dict(row_m.get("decision_state") or {}) if row_m else {}
                    state_m["number_ledger"] = self.number_ledger.to_dict()
                    await self.repo.update_run(run_id, decision_state=state_m)

        row = await self.repo.get_run(run_id)
        state = dict(row.get("decision_state") or {}) if row else {}
        state["disputes"] = [
            {
                "dispute_id": o.dispute.dispute_id,
                "question": o.dispute.question[:200],
                "status": o.dispute.status,
                "rounds": o.dispute.round,
                "participants": o.dispute.participants,
                "stop_reason": o.stop_reason,
            }
            for o in outcomes
        ]
        await self.repo.update_run(run_id, decision_state=state, status="debated")
        self.disputes = outcomes
        yield make_product_event(run_id, "run_log", {
            "note": "debate_complete",
            "disputes": len(outcomes),
            "resolved": sum(1 for o in outcomes if o.dispute.status == "resolved"),
            "narrowed": sum(1 for o in outcomes if o.dispute.status == "narrowed"),
            "retained": sum(1 for o in outcomes if o.dispute.status == "retained"),
        })

    # ============================================================
    # Phase 6：Evidence Audit → Finalization Gate → 最终报告
    # ============================================================

    async def run_finalize(
        self,
        *,
        budget_exhausted: bool = False,
        cancel_requested: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        """审计 → 修订 → 合成 → Gate 裁决（spec §7.5/§12.4/§16/§18）。

        出口（§7.5）：FINALIZED / PARTIAL_FINALIZED / WAITING_USER /
        CANCELLED。预算耗尽不再直接 failed——产出安全有限的阶段性报告。
        """
        from app.decision_harness.finalization import (
            EvidenceAuditor,
            GateInput,
            build_final_report,
            evaluate_gate,
        )

        run_id = self.run_id
        results = getattr(self, "expert_results", None) or []
        if not results:
            rows = await self.repo.list_agent_runs(run_id)
            results = [
                SubAgentResult.model_validate(r["result"])
                for r in rows
                if r.get("agent_type") == "expert" and r.get("result")
            ]
        disputes_state = []
        row = await self.repo.get_run(run_id)
        if row and row.get("decision_state"):
            disputes_state = row["decision_state"].get("disputes", [])
        kernel = getattr(self, "evidence_kernel", None)
        plan = (self.outcome.plan if self.outcome and self.outcome.plan else None) \
            or (PlanSpec.model_validate(row["active_plan"])
                if row and row.get("active_plan") else None)

        # ---- Evidence Audit ----
        await self.repo.update_run(run_id, workflow_phase=WorkflowPhase.EVIDENCE_AUDIT.value)
        yield make_product_event(run_id, "run_log", {"phase": WorkflowPhase.EVIDENCE_AUDIT.value})
        audit_passed = True
        unverified_unknowns: list[str] = []
        if kernel is not None:
            auditor = EvidenceAuditor(
                run_id=run_id, kernel=kernel, repo=self.repo,
                core_claim_ids=[c.claim_id for c in kernel.claims],
            )
            async for event in auditor.run():
                yield event
            audit_passed = auditor.outcome.passed
            if not auditor.outcome.passed:
                # 定向修订已由 auditor 完成（拒绝+重算）；汇总未决 unknowns
                await self.repo.update_run(
                    run_id, workflow_phase=WorkflowPhase.TARGETED_REPAIR.value
                )
                yield make_product_event(run_id, "run_log", {
                    "note": "targeted_repair",
                    "rejected": auditor.outcome.rejected_ids,
                })
            # 搜索过但查不到证据的事实缺口 → 转问用户（第二次机会），
            # 不再卡 Gate；只有证据互相矛盾（DISPUTED）的才算未处理缺口
            unverified_unknowns = []
            ask_user_later = []
            for c in kernel.claims:
                verdict = kernel.verdict(c.claim_id)
                status_value = getattr(verdict.status, "value", str(verdict.status))
                if status_value == "disputed":
                    unverified_unknowns.append(c.text)
                elif status_value == "unverified":
                    ask_user_later.append(c.text)
            if ask_user_later:
                state_q = dict(row.get("decision_state") or {}) if row else {}
                merged_q = list(dict.fromkeys(
                    (state_q.get("questions_for_user") or []) + ask_user_later[:6]
                ))[:8]
                state_q["questions_for_user"] = merged_q
                await self.repo.update_run(run_id, decision_state=state_q)

        # ---- Synthesis ----
        ledger_obj = getattr(self, "number_ledger", None)
        if ledger_obj is None and row:
            from app.decision_harness.number_ledger import NumberLedger
            ledger_obj = NumberLedger.from_dict(
                (row.get("decision_state") or {}).get("number_ledger"))
            self.number_ledger = ledger_obj
        ledger_snapshot_dict = ledger_obj.to_dict() if ledger_obj else None
        ledger_conflict_labels = (
            [c.label for c in ledger_obj.unresolved_conflicts()]
            if ledger_obj else []
        )
        await self.repo.update_run(run_id, workflow_phase=WorkflowPhase.SYNTHESIS_LOOP.value)
        yield make_product_event(run_id, "run_log", {"phase": WorkflowPhase.SYNTHESIS_LOOP.value})
        needs_user = any(d.get("status") == "narrowed" for d in disputes_state) or bool(
            results and any(r.questions_for_user for r in results if r.status == "silent")
        )
        pending_rows = [
            r for r in await self.repo.list_agent_runs(run_id)
            if r.get("status") == "pending_phase2"
        ]
        report = build_final_report(
            decision_options=self.decision.options,
            results=results,
            disputes=disputes_state,
            unknowns=unverified_unknowns + list(self.decision.unknowns),
            gate_failures=[],
            partial=budget_exhausted,
            ledger_dict=ledger_snapshot_dict,
            ledger_conflicts=ledger_conflict_labels,
        )
        yield make_product_event(run_id, "finalization_proposed", {
            "recommendation_strength": report.recommendation.strength,
        })

        # ---- Gate ----
        await self.repo.update_run(run_id, workflow_phase=WorkflowPhase.FINALIZATION_GATE.value)
        from app.decision_harness.claim_router import split_unknowns

        fact_gaps = split_unknowns(
            [t for t in (plan.blocking_unknowns if plan else []) if t]
        )[1]
        gate = evaluate_gate(GateInput(
            decision_options=self.decision.options,
            results=results,
            open_disputes=sum(1 for d in disputes_state if d.get("status") == "open"),
            needs_user=needs_user,
            audit_passed=audit_passed,
            pending_tasks=len(pending_rows),
            report=report,
            blocking_unknowns=fact_gaps,  # 用户型缺口走 questions_for_user，不拦 Gate
            unverified_unknowns=unverified_unknowns,
        ), budget_exhausted=budget_exhausted)
        # CANCELLED：用户主动停止，保留已完成结果（不进 failed 分支）
        if cancel_requested:
            gate_status = RunExitStatus.CANCELLED
        else:
            gate_status = gate.status
        if gate_status == RunExitStatus.PARTIAL_FINALIZED and not gate.failures:
            report = build_final_report(
                decision_options=self.decision.options,
                results=results,
                disputes=disputes_state,
                unknowns=unverified_unknowns + list(self.decision.unknowns),
                gate_failures=["budget_exhausted"],
                partial=True,
                ledger_dict=ledger_snapshot_dict,
                ledger_conflicts=ledger_conflict_labels,
            )

        status_map = {
            RunExitStatus.FINALIZED: "finalized",
            RunExitStatus.PARTIAL_FINALIZED: "partial_finalized",
            RunExitStatus.WAITING_USER: "waiting_user",
            RunExitStatus.CANCELLED: "cancelled",
            RunExitStatus.FAILED: "failed",
        }
        run_status = status_map[gate_status]
        final_event = {
            RunExitStatus.FINALIZED: "decision_finalized",
            RunExitStatus.PARTIAL_FINALIZED: "partial_finalized",
        }.get(gate_status)

        state = dict(row.get("decision_state") or {}) if row else {}
        state["final_report"] = report.model_dump(mode="json")
        state["gate_failures"] = gate.failures
        await self.repo.update_run(
            run_id, decision_state=state, status=run_status,
            exit_status=gate_status.value,
            finalization_state={"failures": gate.failures,
                                "budget_exhausted": budget_exhausted},
        )
        self.final_report = report
        self.exit_status = gate_status
        if final_event:
            yield make_product_event(run_id, final_event, {
                "recommendation": report.recommendation.option[:200],
                "strength": report.recommendation.strength,
                "uncertainties": len(report.remaining_uncertainties),
                "chair_summary": report.chair_summary[:600],
            })
        elif gate_status == RunExitStatus.WAITING_USER:
            yield make_product_event(run_id, "user_input_requested", {
                "questions": [
                    q for r in results for q in r.questions_for_user
                ][:3] or ["请补充会改变建议的关键信息。"],
            })
        else:
            yield make_product_event(run_id, "run_log", {
                "note": "run_cancelled", "preserved_results": len(results),
            })
