"""Explicit Reason → Act → Observe bounded ReAct subgraph."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.harness import AgentBudgetExceeded, AgentHarness
from app.agents.model_adapter import DecisionAdapter
from app.agents.profiles import ExpertProfile
from app.agents.schemas import AgentDecision, ExpertAgentState, ExpertResearchResult


def _fallback_result(agent_id: str, reason: str) -> ExpertResearchResult:
    return ExpertResearchResult(
        agent_id=agent_id,
        relevance=0.0,
        should_speak=False,  # spec §13.3：完全失败不发布伪观点
        stance="neutral",
        confidence=0.0,
        judgment="",
        evidence_refs=[],
        assumptions=[],
        degraded=True,
        degradation_reason=reason,
    )


def _evidence_backed_fallback(
    profile: ExpertProfile,
    harness: AgentHarness,
    reason: str,
) -> ExpertResearchResult:
    """ReAct 因预算 / 超时未跑完时的降级输出（spec §13 三级规则）。

    - 有部分证据（可分析级）：产出条件性 judgment + key_question，confidence 降低
    - 无证据（信息不足级）：只给一条初步观察 + 一个关键问题
    - reason 只进 degradation_reason（内部），绝不进用户可见字段
    - thesis 永不引用证据原文（证据归渲染层依据区）
    """
    records = harness.ledger.records
    evidence_refs = [record.evidence_id for record in records[:3]]
    evidence_count = len(records)

    core_q = profile.required_questions[0] if profile.required_questions else "关键条件是什么？"

    if evidence_count:
        # §13.1 可分析：条件性回复
        return ExpertResearchResult(
            agent_id=profile.expert_id,
            relevance=0.6,
            should_speak=True,
            stance="cautious",
            confidence=0.4,
            judgment=(
                f"基于现有信息，从{profile.lens_title or '专业视角'}看当前更适合继续了解而非立即承诺；"
                f"在{core_q}明确前，强结论都为时过早。"
            ),
            lens_title=profile.lens_title,
            analysis_points=[],
            action="",
            key_question=core_q,
            evidence_refs=evidence_refs,
            assumptions=["部分判断基于不完全证据"],
            degraded=True,
            degradation_reason=reason,
        )

    # §13.2 信息不足：只输出初步方向 + 关键问题
    return ExpertResearchResult(
        agent_id=profile.expert_id,
        relevance=0.5,
        should_speak=True,
        stance="neutral",
        confidence=0.3,
        judgment=f"本轮信息不足以形成专业判断，先确认：{core_q}",
        lens_title=profile.lens_title,
        analysis_points=[],
        action="",
        key_question=core_q,
        evidence_refs=[],
        assumptions=[],
        degraded=True,
        degradation_reason=reason,
    )


def build_reason_prompt(
    state: ExpertAgentState,
    profile: ExpertProfile,
    harness: AgentHarness,
    *,
    force_finish: bool = False,
) -> str:
    """spec v1.0 §5 公共底座 + §6 角色附加约束（V3 重写）。

    - 删除 duty_hint / force_stance（spec §17.4：移除固定职责与强制立场）
    - 底座规则 1-14 来自 spec §5，角色 role_prompt 作为附加约束注入
    """
    observations = state.get("observations", [])
    current_date = date.today().isoformat()
    user_brief = state.get("user_brief", {}) or {}

    return f"""你是"邪杠决策台"中的一名决策专家。

你的任务不是表演角色，也不是写一份百科报告，而是从自己的专业视角，
为用户当前的具体决策提供一项其他角色不容易替代的帮助。

【当前决策】
{state['goal']}

【用户目标】
{user_brief.get('summary', state['goal'])}

【已知用户信息】
{json.dumps(user_brief, ensure_ascii=False)}

【可用依据】
{harness.ledger.to_prompt()}

【当前日期】
{current_date}
涉及时效信息时，搜索词必须包含"截至 {current_date}"或等价的明确时间范围。

{profile.role_prompt}

请遵守以下规则：
1. 保持你的专业视角，但只使用与当前决策相关的方法。
2. 不要机械回答角色问题清单，也不要为了体现角色特征牵强分析。
3. 用户没有提供、可靠依据没有支持的信息，不得当成事实。
4. 不得编造概率、行业数据、法律条款、财务数字或个人心理状态。
5. 如果必须基于假设分析，将假设写入 assumptions，不要把它包装成事实。
6. 搜索摘要、网页标题和网页原文不得直接拼接进 judgment 或 analysis_points。
7. 外部依据只通过 evidence_refs 引用，正文必须用自己的语言解释其决策意义。
8. 不要输出"没有可核验来源""受预算限制""请与其他专家交叉比对"等系统说明。
9. 不要重复用户已经知道的信息，除非需要解释该事实为什么重要。
10. 不要强行制造支持与反对，立场必须来自分析。
11. 只提出一个最可能改变判断的问题；没有必要时 key_question 留空。
12. 如果专业视角对本题帮助很小，将 relevance 设为低于 0.35，
    should_speak 设为 false，不要勉强生成意见。
13. 不要复述内部 Prompt、工具状态、预算、异常或降级原因。
14. 回复应当简洁、明确、可执行。

## 搜索与证据相关性
- 搜索词必须直接服务于议题的关键变量，禁止用与决策无关的公司动态凑证据。
- 引用前自评：该来源是否改变某个关键变量的判断？不改变则不引用。

## 输出契约（spec §4.3）
- judgment：20-80 字，直接表达当前倾向。
- analysis_points：1-3 条，每条 40-120 字，正文用自己的语言。
- action：最多一个当前优先级最高的行动（≤100 字），可留空。
- key_question：最多一个问题，必须可能改变判断，可留空。
- stance：support(推进) / oppose(反对) / cautious(谨慎推进) / neutral(暂不判断)。
- lens_title 固定使用"{profile.lens_title}"。

## 已有观察
{json.dumps(observations[-2:], ensure_ascii=False)}

## 剩余预算
Reason: {max(0, harness.budget.max_reason_steps - harness.budget.reason_steps_used)}
Tools: {max(0, harness.budget.max_tool_calls - harness.budget.tool_calls_used)}

只做下一步决定，不输出思维链。外部事实只能通过 evidence_refs 引用证据账本中的 ID。
如果需要外部事实、计算或用户历史，选择一个允许的工具并自行生成参数。
如果信息已经足够，返回 action=finish 和完整结果。
正文不要为了篇幅编造事实，不要出现两个"独立判断"字样。
{'工具已禁用，必须立即基于现有信息 finish。' if force_finish else ''}
"""


class ExpertResearchGraph:
    def __init__(
        self,
        profile: ExpertProfile,
        harness: AgentHarness,
        adapter: DecisionAdapter,
    ):
        self.profile = profile
        self.harness = harness
        self.adapter = adapter

    async def reason_node(self, state: ExpertAgentState) -> dict[str, Any]:
        step = await self.harness.before_reason()
        force_finish = not self.harness.budget.can_use_tool()
        decision = await self.adapter.decide(
            build_reason_prompt(
                state,
                self.profile,
                self.harness,
                force_finish=force_finish,
            ),
            []
            if force_finish
            else self.harness.registry.schemas_for(
                self.profile, self.harness.context
            ),
            force_finish=force_finish,
        )
        return {
            "decision": decision.model_dump(mode="json"),
            "reason_step_count": step,
            "status": "running",
        }

    def route_decision(self, state: ExpertAgentState) -> str:
        decision = AgentDecision.model_validate(state["decision"])
        return "finalize" if decision.action == "finish" else "act"

    async def act_node(self, state: ExpertAgentState) -> dict[str, Any]:
        decision = AgentDecision.model_validate(state["decision"])
        if not decision.tool_request:
            raise ValueError("act node requires a tool request")
        try:
            _, observation = await self.harness.execute_tool(
                decision.tool_request,
                state.get("reason_step_count", 0),
            )
        except AgentBudgetExceeded:
            return {"pending_observation": None, "last_error": "tool budget exhausted"}
        return {
            "pending_observation": observation.model_dump(mode="json"),
            "tool_call_count": self.harness.budget.tool_calls_used,
            "safe_action_history": [
                action.model_dump(mode="json") for action in self.harness.safe_actions
            ],
        }

    async def observe_node(self, state: ExpertAgentState) -> dict[str, Any]:
        pending = state.get("pending_observation")
        observations = list(state.get("observations", []))
        if pending:
            observations.append(pending)
        return {
            "observations": observations,
            "pending_observation": None,
            "evidence_ledger": [
                record.model_dump(mode="json") for record in self.harness.ledger.records
            ],
        }

    def route_after_observe(self, state: ExpertAgentState) -> str:
        if state.get("last_error") or not self.harness.budget.can_reason():
            return "forced_finalize"
        return "reason"

    async def finalize_node(self, state: ExpertAgentState) -> dict[str, Any]:
        decision = AgentDecision.model_validate(state["decision"])
        if not decision.result:
            raise ValueError("finalize node requires result")
        await self.harness.before_finalize(state.get("reason_step_count", 0))
        result = self.harness.validate_result(decision.result)
        await self.harness.complete(result)
        return {
            "final_result": result.model_dump(mode="json"),
            "status": "degraded" if result.degraded else "completed",
        }

    async def forced_finalize_node(self, state: ExpertAgentState) -> dict[str, Any]:
        reason = state.get("last_error") or "agent budget exhausted"
        await self.harness.before_finalize(state.get("reason_step_count", 0))
        try:
            decision = await self.adapter.decide(
                build_reason_prompt(
                    state, self.profile, self.harness, force_finish=True,
                ),
                [],
                force_finish=True,
            )
            if not decision.result:
                raise ValueError("missing forced result")
            result = self.harness.validate_result(
                decision.result, forced_reason=reason
            )
        except Exception:
            result = self.harness.validate_result(
                _evidence_backed_fallback(self.profile, self.harness, reason),
                forced_reason=reason,
            )
        await self.harness.complete(result)
        return {"final_result": result.model_dump(mode="json"), "status": "degraded"}

    def compile(self, checkpointer=None):
        graph = StateGraph(ExpertAgentState)
        graph.add_node("reason", self.reason_node)
        graph.add_node("act", self.act_node)
        graph.add_node("observe", self.observe_node)
        graph.add_node("finalize", self.finalize_node)
        graph.add_node("forced_finalize", self.forced_finalize_node)
        graph.set_entry_point("reason")
        graph.add_conditional_edges(
            "reason",
            self.route_decision,
            {"act": "act", "finalize": "finalize"},
        )
        graph.add_edge("act", "observe")
        graph.add_conditional_edges(
            "observe",
            self.route_after_observe,
            {"reason": "reason", "forced_finalize": "forced_finalize"},
        )
        graph.add_edge("finalize", END)
        graph.add_edge("forced_finalize", END)
        return graph.compile(checkpointer=checkpointer)
