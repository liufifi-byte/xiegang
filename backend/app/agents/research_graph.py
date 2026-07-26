"""Explicit Reason → Act → Observe bounded ReAct subgraph."""

from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.harness import AgentBudgetExceeded, AgentHarness
from app.agents.model_adapter import DecisionAdapter
from app.agents.profiles import ExpertProfile
from app.agents.schemas import AgentDecision, ExpertAgentState, ExpertResearchResult


def _fallback_result(agent_id: str, reason: str) -> ExpertResearchResult:
    return ExpertResearchResult(
        agent_id=agent_id,
        stance="neutral",
        confidence=0.25,
        thesis="现有信息不足以形成高置信度结论。",
        evidence_refs=[],
        assumptions=[],
        unknowns=[reason],
        reversal_conditions=["补充关键事实后重新分析"],
        next_actions=["补充最影响结论的事实"],
        degraded=True,
        degradation_reason=reason,
    )


def _evidence_backed_fallback(
    profile: ExpertProfile,
    harness: AgentHarness,
    reason: str,
) -> ExpertResearchResult:
    records = harness.ledger.records
    evidence_refs = [record.evidence_id for record in records[:3]]
    if records:
        evidence_summary = "；".join(
            record.claim_summary[:100] for record in records[:2]
        )
        thesis = (
            f"已完成的调研或计算显示：{evidence_summary}。"
            f"结合{profile.analysis_methods[0]}，这些信息仍不足以单独决定最终选择。"
        )
    else:
        thesis = (
            f"本轮未获得足够的可验证资料；应先用{profile.analysis_methods[0]}"
            "补齐最影响结论的变量，再做最终选择。"
        )
    return ExpertResearchResult(
        agent_id=profile.expert_id,
        stance="neutral",
        confidence=0.45 if records else 0.25,
        thesis=thesis,
        evidence_refs=evidence_refs,
        assumptions=["已获得的工具结果只覆盖当前可验证信息"],
        unknowns=[reason],
        reversal_conditions=[profile.required_questions[0]],
        next_actions=list(profile.required_questions[:2]),
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
    observations = state.get("observations", [])
    return f"""你是 Decision Council 的{profile.display_name}，正在执行一次有边界的自主调研。

## 目标
{state['goal']}

## 用户简报
{json.dumps(state.get('user_brief', {}), ensure_ascii=False)}

## 专业身份
{profile.professional_identity}

## 专业方法
{'; '.join(profile.analysis_methods)}

## 表达方式
{'; '.join(profile.communication_style)}

## 必须检查
{'; '.join(profile.required_questions)}

## 专业边界
{'; '.join(profile.forbidden_inferences + profile.professional_boundaries)}

## 已有观察
{json.dumps(observations[-2:], ensure_ascii=False)}

## 可引用证据账本
{harness.ledger.to_prompt()}

## 剩余预算
Reason: {max(0, harness.budget.max_reason_steps - harness.budget.reason_steps_used)}
Tools: {max(0, harness.budget.max_tool_calls - harness.budget.tool_calls_used)}

只做下一步决定，不输出思维链。外部事实只能通过 evidence_refs 引用证据账本中的 ID。
没有可核验证据时，把相关内容写入 assumptions，不得生成精确市场数据、最新数据或历史统计。
你可以不给出确定的支持或反对，但必须给出清晰的条件判断和结论反转条件。
如果需要外部事实、计算或用户历史，选择一个允许的工具并自行生成参数。
如果信息已经足够，返回 action=finish 和完整结果。
最终结果必须包含至少一个 reversal_conditions。
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
                    state, self.profile, self.harness, force_finish=True
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
