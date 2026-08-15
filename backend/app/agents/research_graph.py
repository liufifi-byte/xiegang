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
    """当 ReAct 因预算 / 超时未跑完时，产出职业视角的方向性判断。

    P0 修订（第二轮 review）：
    - thesis 绝不引用证据原文——之前"基于已收集的 N 项可核验信息（原文前100字）"
      把网页口语化文本直接塞进判断段落，与下方【关键依据】整段重复，观感灾难。
      现在 thesis 只写职业视角的方向性结论，证据一律由渲染层在证据区展示。
    - 用户可见 unknown 不暴露 reason 字面值；内部 reason 只进 degradation_reason。
    """
    records = harness.ledger.records
    evidence_refs = [record.evidence_id for record in records[:3]]
    evidence_count = len(records)

    # 用户友好的 unknown（不暴露 reason 字面值）。
    user_facing_unknown = (
        "受本次调研步骤预算或时间限制，未能完成全面取证；"
        "建议读者把以下判断视为初步结论而非完整推理。"
    ) if evidence_count == 0 else (
        "调研在收尾阶段受限，部分判断基于不完全证据，请与其他专家交叉比对。"
    )

    method = profile.analysis_methods[0] if profile.analysis_methods else "专业分析"
    if evidence_count:
        # 有部分证据：写职业视角的方向性判断，不引用原文
        thesis = (
            f"我从{method}视角看，当前信号更倾向于谨慎：已核验的 {evidence_count} 项信息"
            f"（见下方依据区）尚未支撑一个高置信结论。"
            f"若你最看重的变量是{'、'.join(profile.required_questions[:1]) or '核心条件'}，"
            f"请把它补全后再比较两个选项的期望值。"
        )
        confidence = 0.45
    else:
        thesis = (
            f"本轮未能完成取证，我基于你提供的信息从{method}视角只能给出方向性参考："
            f"在你补齐{'、'.join(profile.required_questions[:2]) or '关键事实'}之前，"
            f"任何一边的强结论都为时过早。"
        )
        confidence = 0.25

    return ExpertResearchResult(
        agent_id=profile.expert_id,
        stance="neutral",
        confidence=confidence,
        thesis=thesis,
        evidence_refs=evidence_refs,
        assumptions=["已获得的工具结果只覆盖当前可验证信息"],
        unknowns=[user_facing_unknown],
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
    duty_hint: str | None = None,
    force_stance: str | None = None,
) -> str:
    observations = state.get("observations", [])
    current_date = date.today().isoformat()

    # 议会职责段（duties 之前是 dead code——现在激活让它真正影响立场倾向）
    duty_block = ""
    if duty_hint:
        duty_block = f"""
## 你的议会职责
你本场被分配到【{duty_hint}】职责。请让此职责引导你的审查重点和初始立场倾向。
职责只改变审查重点，不覆盖你的职业人格。
"""
    # 强制立场（用于重跑扎堆时引导分散；不强制具体 thesis，只要求 stance 方向）
    force_stance_block = ""
    if force_stance:
        stance_zh = {"for": "支持", "against": "反对", "neutral": "暂不作绝对判断"}.get(force_stance, force_stance)
        force_stance_block = f"""
## 立场要求（本轮重新审查）
本次议会总体立场分布过于集中，需要你从【{duty_hint}】职责的视角重新审视后，把 stance 调整为「{stance_zh}」。
- 基于你已有的证据/假设给出能支撑此立场的条件判断
- 不得编造事实，证据不足时明确说明是 conditional / neutral
- 你的 thesis 必须围绕这个立场的核心论证展开，而不是回避
"""

    return f"""你是 Decision Council 的{profile.display_name}，正在执行一次有边界的自主调研。

## 目标
{state['goal']}

## 当前日期
{current_date}
涉及价格、政策、市场、人物或其他时效信息时，搜索词必须包含“截至 {current_date}”或等价的明确时间范围。

## 用户简报
{json.dumps(state.get('user_brief', {}), ensure_ascii=False)}

## 专业身份
{profile.professional_identity}

## 专业方法
{'; '.join(profile.analysis_methods)}

## 表达方式
{'; '.join(profile.communication_style)}

## 表达克制
- 禁止戏剧化比喻（"自断后路"、"防御性自杀"、"达摩克利斯之剑"等）和情感操控词。
- 保持专业冷静，中性陈述事实与推论；锐度来自事实力度，不来自修辞。
- 全文中文；直接对用户用"你"，不用"用户"称呼决策者；不写 markdown 星号。
- 不写 [ev_xxx] 之类内部编号，引用证据时说"依据 N"。

## 搜索与证据相关性
- 搜索词必须直接服务于议题的关键变量（如竞业风险、初创存活率、薪酬结构），
  禁止用与决策无关的公司动态（招聘规模、年会新闻、品牌宣传）凑证据。
- 引用前自评：该来源是否改变某个关键变量的判断？不改变则不引用。

## 你的腔调范例
模仿以下发言的锐度和判断密度，但不要照搬具体内容，也不要复制其修辞风格：
{chr(10).join(profile.voice_examples)}
{duty_block}{force_stance_block}
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
最终 thesis 应为 180-280 字的核心专业判断；同时完整填写风险、假设、未知条件、翻转条件和下一步动作，供系统合成为 4 段公开独立判断。
thesis 不要重复其他字段，不要为了篇幅编造事实。
最终结果必须包含至少一个 reversal_conditions。
{'工具已禁用，必须立即基于现有信息 finish。' if force_finish else ''}
"""


class ExpertResearchGraph:
    def __init__(
        self,
        profile: ExpertProfile,
        harness: AgentHarness,
        adapter: DecisionAdapter,
        *,
        duty_hint: str | None = None,
        force_stance: str | None = None,
    ):
        self.profile = profile
        self.harness = harness
        self.adapter = adapter
        self.duty_hint = duty_hint
        self.force_stance = force_stance

    async def reason_node(self, state: ExpertAgentState) -> dict[str, Any]:
        step = await self.harness.before_reason()
        force_finish = not self.harness.budget.can_use_tool()
        decision = await self.adapter.decide(
            build_reason_prompt(
                state,
                self.profile,
                self.harness,
                force_finish=force_finish,
                duty_hint=self.duty_hint,
                force_stance=self.force_stance,
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
                    duty_hint=self.duty_hint,
                    force_stance=self.force_stance,
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
