"""Context Compiler（spec 2026-08-22 §11.2-11.3，Phase 2）。

三层职责：
1. 把 Canonical State（决策 + 计划 + 用户事实）编译成子 Agent 的 ContextPacket；
2. 构造 ExpertTaskPacket（复用产品侧八人库 Profile 作为 role_contract）；
3. 渲染子 Agent prompt 与 Root 侧观察（Root 只看结构化结论，绝不吸收
   Child 原始历史——spec §13.3 / §11.5 防趋同）。

隔离铁律：编译输入永远不含其他专家的 transcript；专家间共享的只有
结构化 SubAgentResult 摘要，且由 Root 决定是否、何时、以何种裁剪注入。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.agents.profiles import EXPERT_PROFILES
from app.decision_harness.schemas import (
    ContextPacket,
    DecisionModel,
    ExpertTaskPacket,
    PlanSpec,
    SubAgentResult,
)

# Root 观察里允许出现的 SubAgentResult 字段（白名单——加字段须同步评审 §15.2）
ROOT_VISIBLE_RESULT_FIELDS = (
    "task_id",
    "agent_id",
    "status",
    "relevance",
    "stance",
    "judgment",
    "confidence",
    "unknowns",
    "questions_for_user",
)


def _sha256_sections(sections: dict[str, Any]) -> str:
    canonical = json.dumps(sections, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_expert_task_packet(
    *,
    task_id: str,
    expert_id: str,
    objective: str,
    decision: DecisionModel,
    plan: PlanSpec,
    context_version: int,
) -> ExpertTaskPacket:
    """从 Canonical State 构造专家任务包（Phase 0 契约的落地工厂）。

    objective 会经过 ExpertTaskPacket 校验（八人库内 + 具体性），
    泛泛的"请分析这个问题"在这里就被拒绝，不会流到子 Agent。
    """
    profile = EXPERT_PROFILES.get(expert_id)
    if profile is None:
        raise ValueError(f"未知专家 ID: {expert_id!r}（八人库之外）")
    return ExpertTaskPacket(
        task_id=task_id,
        expert_id=expert_id,
        objective=objective,
        decision_options=decision.options,
        relevant_user_facts=decision.verified_user_facts[:20],
        relevant_evidence=[],  # Phase 4 接 Evidence Kernel 后填充
        known_unknowns=(plan.blocking_unknowns + plan.non_blocking_unknowns)[:10],
        role_contract={
            "display_name": profile.display_name,
            "lens_title": profile.lens_title,
            "core_question": profile.core_question,
            "professional_identity": profile.professional_identity,
            "analysis_methods": list(profile.analysis_methods),
            "completion_criteria": list(profile.completion_criteria),
            "forbidden_inferences": list(profile.forbidden_inferences),
            "signature_line": profile.signature_line,
        },
        allowed_tools=list(profile.allowed_tools),
        forbidden_actions=[
            "fabricate_numbers",
            "access_shell",
            "write_files",
            "modify_canonical_state",
        ],
        expected_output={
            "format": "SubAgentResult JSON",
            "fields": ["judgment", "analysis_points", "unknowns", "action",
                       "reversal_conditions", "confidence"],
        },
        budget={"max_turns_hint": 4},
        context_version=context_version,
    )


def compile_child_context(
    packet: ExpertTaskPacket,
    *,
    decision: DecisionModel,
    plan: PlanSpec,
    evidence: list[dict[str, Any]] | None = None,
) -> ContextPacket:
    """编译子 Agent 上下文包（spec §11.2：带版本与内容哈希）。

    sections 固定七节：decision / task / user_facts / evidence / unknowns /
    role_contract / budget。evidence 只放可用证据（verified/corroborated）
    的摘要白名单（无 excerpt，§15.2）。这里没有任何其他专家的输出——
    那是 Root 在辩论阶段（Phase 5）的显式决策，不在编译层隐式注入。
    """
    sections: dict[str, Any] = {
        "decision": {
            "question": decision.question,
            "options": decision.options,
            "constraints": decision.constraints,
            "user_goals": decision.user_goals,
            "risk_level": decision.risk_level,
            "decision_deadline": (
                decision.decision_deadline.isoformat()
                if decision.decision_deadline
                else None
            ),
        },
        "task": {
            "task_id": packet.task_id,
            "objective": packet.objective,
            "expert_id": packet.expert_id,
        },
        "user_facts": {
            "verified": packet.relevant_user_facts,
            "preferences": decision.preferences[:12],
        },
        "evidence": evidence or [],
        "unknowns": packet.known_unknowns,
        "role_contract": packet.role_contract,
        "budget": {
            "packet_budget": packet.budget,
            "stop_conditions": plan.stop_conditions[:10],
        },
    }
    return ContextPacket(
        task_id=packet.task_id,
        compiled_for=packet.expert_id,
        context_version=packet.context_version,
        content_sha256=_sha256_sections(sections),
        sections=sections,
    )


def render_child_prompt(packet: ExpertTaskPacket, context: ContextPacket) -> str:
    """子 Agent 首轮 prompt：上下文包 + 输出契约（只输出 SubAgentResult JSON）。"""
    context_json = json.dumps(context.sections, ensure_ascii=False, indent=2)
    return (
        f"【任务上下文包 v{context.context_version}】（sha256:{context.content_sha256[:12]}）\n"
        f"{context_json}\n\n"
        "【输出契约】完成分析后，只输出一个 SubAgentResult JSON 对象"
        "（不要输出其他文本、不要 markdown 围栏），字段：\n"
        '{"status":"completed|partial|silent","relevance":0.0-1.0,'
        '"stance":"support|oppose|cautious|neutral","judgment":"一句话判断",'
        '"analysis_points":["最多3条"],"unknowns":["关键不确定项"],'
        '"action":"建议行动","reversal_conditions":["推翻条件"],'
        '"questions_for_other_agents":[],"questions_for_user":["最多2个"],'
        '"confidence":0.0-1.0}\n'
        "silent 时不得携带观点（judgment 留空、analysis_points 为空）。"
        "禁止编造数字与来源；未核实的表述放进 unknowns。"
    )


def render_child_finalize_prompt(packet: ExpertTaskPacket) -> str:
    """预算将尽时的强制收敛 prompt（对应 partial 退出路径）。"""
    return (
        f"任务 {packet.task_id} 的轮次预算已用完。现在立即只输出最终 "
        "SubAgentResult JSON（格式同前）。已有把握的写进 judgment，"
        "没把握的必须写进 unknowns；信息不足以支撑判断时 status 用 "
        '"partial"，判断不完整时不要假装完整。'
    )


def root_observation(results: list[SubAgentResult]) -> dict[str, Any]:
    """专家结果 → Root 侧结构化观察（spec §13.3：Root 不吸收 Child 原始历史）。

    只保留白名单字段；analysis_points 等长内容不进 Root 上下文
    （需要时 Root 通过 follow-up 向该 Child 的线程追问，Phase 2+）。
    """
    return {
        "kind": "expert_results",
        "results": [
            {k: getattr(r, k) for k in ROOT_VISIBLE_RESULT_FIELDS}
            for r in results
        ],
        "summary": {
            "total": len(results),
            "completed": sum(1 for r in results if r.status == "completed"),
            "partial": sum(1 for r in results if r.status == "partial"),
            "silent": sum(1 for r in results if r.status == "silent"),
            "failed": sum(1 for r in results if r.status == "failed"),
        },
    }
