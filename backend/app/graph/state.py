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
    decision_model: dict[str, Any]
    evidence_pool: list[dict[str, Any]]

    # === 本场职业专家 ===
    selected_expert_ids: list[str]
    expert_selection_reasons: dict[str, str]
    # V3: debate_duties 已删除（spec §17.4）——键保留避免旧 checkpoint 断裂
    divergent_expert_ids: list[str]
    core_disputes: list[dict[str, Any]]
    round_decisions: list[dict[str, Any]]
    needs_third_round: bool
    basis_assessment: dict[str, Any]
    agent_results: dict[str, dict]
    agent_traces: list[dict]
    research_degraded: bool
    research_error: str | None

    # === 专家调研前的信息门槛 ===
    clarification_status: str
    clarification_questions: list[dict[str, Any]]
    clarification_answers: list[dict[str, Any]]
    clarification_reason: str
    clarification_waiting: bool

    # === 会前两步流（PRD v1.1 §14.3）===
    # P0 修复：这两个 channel 之前只在 make_initial_state 的返回 dict 里，
    # 未在 TypedDict 声明——LangGraph StateGraph 按 schema 过滤输入，
    # 导致字段进 graph 即被丢弃：
    #   1. clarification_gate 的 confirmed 跳过检查永远读不到（重复弹窗根因）
    #   2. parse_topic 的 decision_path 优先分类也从没生效过
    decision_path: dict[str, Any]
    decision_path_status: str

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
    chair: str | None              # P0 step3: 当选议长 expert_id
    chair_name: str | None         # P0 step3: 议长显示名
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
    clarification_status: str = "not_checked",
    clarification_questions: list[dict[str, Any]] | None = None,
    clarification_answers: list[dict[str, Any]] | None = None,
    decision_path: dict[str, Any] | None = None,
    decision_path_status: str = "not_started",
) -> ParliamentState:
    """方便地构造一个干净的初始 state（所有字段都有默认值）。

    PRD v1.1 §14.3: 新增 decision_path / decision_path_status 字段。
    parse_topic 节点会优先读 decision_path_status='confirmed' 跳过 LLM 分类。
    """
    return {
        "topic": topic,
        "user_id": user_id,
        "topic_category": topic_category,
        "user_background": user_background,
        "briefing": {},
        "category": None,
        "decision_model": {
            "question": topic,
            "options": [],
            "user_facts": [],
            "preferences": [],
            "unknowns": [],
            "research_questions": [],
        },
        "evidence_pool": [],
        "selected_expert_ids": list(selected_expert_ids or []),
        "expert_selection_reasons": {},
        "debate_duties": {},
        "divergent_expert_ids": [],
        "core_disputes": [],
        "round_decisions": [],
        "needs_third_round": False,
        "basis_assessment": {
            "level": "limited",
            "verified_count": 0,
            "important_unknown_count": 0,
        },
        "agent_results": {},
        "agent_traces": [],
        "research_degraded": False,
        "research_error": None,
        "clarification_status": clarification_status,
        "clarification_questions": list(clarification_questions or []),
        "clarification_answers": list(clarification_answers or []),
        "clarification_reason": "",
        "clarification_waiting": False,
        "members": {},
        "current_round": 0,
        "max_rounds": 3,
        "tension_score": 0.0,
        "transcript": [],
        "tool_calls": [],
        "election_result": None,
        "chair": None,
        "chair_name": None,
        "summary": None,
        "user_interrupt": None,
        "interrupt_pending": False,
        "parliament_id": parliament_id,
        "started_at": None,
        # PRD v1.1 §14.3
        "decision_path": dict(decision_path or {}),
        "decision_path_status": decision_path_status,
    }
