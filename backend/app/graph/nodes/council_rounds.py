"""Dispute-driven two-to-three round council orchestration."""

from __future__ import annotations

# V3：duties 系统已删除（spec §17.4）
from app.graph.nodes.round_debate import run_debate_round


def _selected(state: dict) -> list[str]:
    return list(state.get("selected_expert_ids") or state.get("members", {}).keys())


async def run_opening_round(state: dict) -> dict:
    """Round one: every selected expert states a position."""
    scoped = dict(state)
    scoped["selected_expert_ids"] = _selected(state)
    scoped["round_focus"] = "opening"
    return await run_debate_round(scoped)


def extract_core_disputes(state: dict) -> dict:
    """Create at most three inspectable disputes without another LLM call."""
    selected = _selected(state)
    members = state.get("members", {})
    stances: dict[str, list[str]] = {}
    for expert_id in selected:
        stance = members.get(expert_id, {}).get("stance", "neutral")
        stances.setdefault(stance, []).append(expert_id)

    variables = list(state.get("briefing", {}).get("key_variables", []))
    if not variables:
        variables = ["核心收益与代价"]
    questions = [
        f"{variable}会在什么条件下改变当前选择？"
        for variable in variables[:3]
    ]

    has_real_disagreement = len(
        [stance for stance, ids in stances.items() if ids]
    ) > 1
    participants: list[str] = []
    if has_real_disagreement:
        for ids in stances.values():
            if ids:
                participants.append(ids[0])
    else:
        # V3：无真实分歧时优先请逆向思考者检查问题定义（原 contrarian_challenge 职责）
        challenger = next(
            (eid for eid in selected if eid == "contrarian"), None
        )
        if challenger:
            participants.append(challenger)
    for expert_id in selected:
        if expert_id not in participants:
            participants.append(expert_id)
        if len(participants) >= 4:
            break

    dispute_kind = "position" if has_real_disagreement else "assumption"
    disputes = [
        {
            "dispute_id": f"dispute_{index + 1}",
            "question": question,
            "kind": dispute_kind,
            "participants": participants,
            "status": "open",
            "pressure_test": not has_real_disagreement,
        }
        for index, question in enumerate(questions)
    ]
    return {"core_disputes": disputes}


def _dispute_participants(state: dict) -> list[str]:
    selected = _selected(state)
    participants: list[str] = []
    for dispute in state.get("core_disputes", []):
        for expert_id in dispute.get("participants", []):
            if expert_id in selected and expert_id not in participants:
                participants.append(expert_id)
            if len(participants) >= 4:
                return participants
    return selected[:4]


async def run_cross_examination_round(state: dict) -> dict:
    """Round two: two to four relevant experts address the core disputes."""
    scoped = dict(state)
    scoped["selected_expert_ids"] = _dispute_participants(state)
    scoped["round_focus"] = "cross_examination"
    return await run_debate_round(scoped)


def assess_third_round(state: dict) -> dict:
    """Decide whether a convergence/pressure-test round adds real value."""
    round_two_entries = [
        entry
        for entry in state.get("transcript", [])
        if entry.get("round") == 2
    ]
    content = " ".join(str(entry.get("content", "")) for entry in round_two_entries)
    changed_position = any(
        marker in content for marker in ("调整", "改变立场", "修正", "被说服")
    )
    user_added_information = any(
        entry.get("type") == "user_interrupt" for entry in round_two_entries
    )
    partial_failure = any(
        entry.get("type") == "debate_error" for entry in round_two_entries
    )
    unresolved_multiple_disputes = len(state.get("core_disputes", [])) > 1
    high_tension = float(state.get("tension_score", 0.0) or 0.0) >= 0.66
    needs_third_round = any(
        (
            changed_position,
            user_added_information,
            partial_failure,
            unresolved_multiple_disputes,
            high_tension,
        )
    )
    reasons = []
    for condition, label in (
        (changed_position, "出现立场变化"),
        (user_added_information, "用户补充了重要信息"),
        (partial_failure, "第二轮有专家未完成回应"),
        (unresolved_multiple_disputes, "仍有多个核心争议"),
        (high_tension, "关键立场仍未收敛"),
    ):
        if condition:
            reasons.append(label)
    return {
        "needs_third_round": needs_third_round,
        "round_decisions": list(state.get("round_decisions", []))
        + [
            {
                "after_round": 2,
                "continue": needs_third_round,
                "reasons": reasons or ["第二轮已经充分收敛"],
            }
        ],
    }


def route_after_round_two(state: dict) -> str:
    if state.get("needs_third_round") and state.get("current_round", 0) < 3:
        return "convergence"
    return "summary"


async def run_convergence_round(state: dict) -> dict:
    """Round three: resolve remaining differences and define reversal conditions."""
    scoped = dict(state)
    scoped["selected_expert_ids"] = _dispute_participants(state)
    scoped["round_focus"] = "convergence"
    result = await run_debate_round(scoped)
    result["core_disputes"] = [
        {**dispute, "status": "reviewed"}
        for dispute in state.get("core_disputes", [])
    ]
    return result


def public_duty_label(state: dict, expert_id: str) -> str:
    # V3：duty 标签删除——返回角色 lens（专属分析栏目）或空
    from app.agents.profiles import get_expert_profile
    try:
        return get_expert_profile(expert_id).lens_title
    except ValueError:
        return ""
