"""议会状态机状态定义。

所有节点共享的 state 结构。LangGraph 用 TypedDict 标注类型。
"""
from typing import Any, TypedDict


class ParliamentState(TypedDict):
    # === 输入 ===
    topic: str
    user_id: str | None
    topic_category: str | None
    user_background: str | None

    # === 主调度分解 ===
    briefing: dict[str, Any]      # {key_variables: [...], summary: "..."}
    category: str | None          # 'real_estate'|'gold'|...|'general'

    # === 本场职业专家 ===
    selected_expert_ids: list[str]
    expert_selection_reasons: dict[str, str]
    debate_duties: dict[str, str]
    agent_results: dict[str, dict]
    agent_traces: list[dict]
    research_degraded: bool
    research_error: str | None

    # === 5 议员状态 ===
    members: dict[str, dict]      # {suan: {stance, evidence, reasoning, ...}}

    # === 流程控制 ===
    current_round: int
    max_rounds: int
    tension_score: float

    # === 输出 ===
    transcript: list[dict]        # [{speaker, round, content, evidence, type}]
    tool_calls: list[dict]        # [{member, tool, args, result, timestamp}]
    election_result: dict | None
    summary: dict | None

    # === 用户干预 ===
    user_interrupt: str | None
    interrupt_pending: bool

    # === 元数据 ===
    parliament_id: str | None
    started_at: float | None


def make_initial_state(
    topic: str,
    *,
    user_id: str | None = None,
    topic_category: str | None = None,
    user_background: str | None = None,
    parliament_id: str | None = None,
    selected_expert_ids: list[str] | None = None,
) -> ParliamentState:
    """方便地构造一个干净的初始 state（所有字段都有默认值）。"""
    return {
        "topic": topic,
        "user_id": user_id,
        "topic_category": topic_category,
        "user_background": user_background,
        "briefing": {},
        "category": None,
        "selected_expert_ids": list(selected_expert_ids or []),
        "expert_selection_reasons": {},
        "debate_duties": {},
        "agent_results": {},
        "agent_traces": [],
        "research_degraded": False,
        "research_error": None,
        "members": {},
        "current_round": 0,
        "max_rounds": 3,
        "tension_score": 0.0,
        "transcript": [],
        "tool_calls": [],
        "election_result": None,
        "summary": None,
        "user_interrupt": None,
        "interrupt_pending": False,
        "parliament_id": parliament_id,
        "started_at": None,
    }
