"""Decision Agent Runtime V4 领域契约（spec 2026-08-22 §5-§12、§18）。

Phase 0 只固定接口与不变量，不改变任何生产行为（DECISION_RUNTIME_MODE=legacy）。
所有模型不依赖 Codex SDK 类型——业务代码只认这里的产品契约。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ============================================================
# 枚举（非法值必须被拒绝，spec §21.1）
# ============================================================

class MainAgentAction(str, Enum):
    """主 Agent 每轮只允许选择一个高层动作（spec §7.1）。"""

    ASK_USER = "ask_user"
    DELEGATE_AGENT = "delegate_agent"
    SEND_FOLLOWUP = "send_followup"
    REQUEST_RESEARCH = "request_research"
    START_DEBATE = "start_debate"
    REQUEST_REVISION = "request_revision"
    RUN_EVIDENCE_AUDIT = "run_evidence_audit"
    REVISE_PLAN = "revise_plan"
    FINALIZE = "finalize"


class SubAgentAction(str, Enum):
    """子 Agent 可选动作（spec §8.1）。"""

    INSPECT_CONTEXT = "inspect_context"
    SEARCH_EVIDENCE = "search_evidence"
    CALCULATE = "calculate"
    QUERY_MEMORY = "query_memory"
    REQUEST_CLARIFICATION = "request_clarification"
    DRAFT_JUDGMENT = "draft_judgment"
    SELF_REVIEW = "self_review"
    FINALIZE = "finalize"


class WorkflowPhase(str, Enum):
    """外层 Workflow 确定性阶段（spec §6）。"""

    INTAKE = "INTAKE"
    PLANNING_LOOP = "PLANNING_LOOP"
    USER_CLARIFICATION = "USER_CLARIFICATION"
    DELEGATION = "DELEGATION"
    EXPERT_LOOPS = "EXPERT_LOOPS"
    DISPUTE_EXTRACTION = "DISPUTE_EXTRACTION"
    DEBATE_LOOP = "DEBATE_LOOP"
    EVIDENCE_AUDIT = "EVIDENCE_AUDIT"
    TARGETED_REPAIR = "TARGETED_REPAIR"
    SYNTHESIS_LOOP = "SYNTHESIS_LOOP"
    FINALIZATION_GATE = "FINALIZATION_GATE"


class RunExitStatus(str, Enum):
    """主 Agent 退出状态（spec §7.5）。FAILED 只在核心运行时无法恢复时使用。"""

    FINALIZED = "FINALIZED"
    PARTIAL_FINALIZED = "PARTIAL_FINALIZED"
    WAITING_USER = "WAITING_USER"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ReplanReason(str, Enum):
    """Replan 必须给出机器可读原因（spec §7.3）。"""

    NEW_USER_FACT = "new_user_fact"
    EVIDENCE_CONFLICT = "evidence_conflict"
    EXPERT_FAILURE = "expert_failure"
    NEW_OPTION_FOUND = "new_option_found"
    MATERIAL_DISAGREEMENT = "material_disagreement"
    BUDGET_PRESSURE = "budget_pressure"
    AUDIT_REJECTION = "audit_rejection"


class ConflictType(str, Enum):
    """争议类型（spec §9.2）。"""

    FACT_CONFLICT = "fact_conflict"
    ASSUMPTION_CONFLICT = "assumption_conflict"
    VALUE_CONFLICT = "value_conflict"
    TIME_HORIZON_CONFLICT = "time_horizon_conflict"
    RISK_TOLERANCE_CONFLICT = "risk_tolerance_conflict"
    OPTION_DEFINITION_CONFLICT = "option_definition_conflict"


class EvidenceStatus(str, Enum):
    """Evidence 状态（spec §12.1）。

    UNVERIFIED / REJECTED / STALE 不得支撑最终报告核心结论
    （由 Evidence Audit 强制，Phase 6 实现；此处先固定枚举）。
    """

    VERIFIED = "verified"
    CORROBORATED = "corroborated"
    DISPUTED = "disputed"
    UNVERIFIED = "unverified"
    REJECTED = "rejected"
    STALE = "stale"


class EvidenceKind(str, Enum):
    USER_FACT = "user_fact"
    WEB_SOURCE = "web_source"
    CALCULATION = "calculation"
    INFERENCE = "inference"


class MemoryKind(str, Enum):
    """用户记忆三类（spec §11.6）。"""

    PROFILE_MEMORY = "profile_memory"
    DECISION_EPISODE = "decision_episode"
    SESSION_MEMORY = "session_memory"


# 专家 ID 契约：与 app/agents/profiles.py 的八人库一致（spec §5.3）
EXPERT_IDS = frozenset(
    {
        "economist",
        "psychologist",
        "historian",
        "mathematician",
        "strategist",
        "legal_scholar",
        "sociologist",
        "contrarian",
    }
)

# 每场专家数量边界（spec §3.1 / §5.3）
MIN_EXPERTS = 3
MAX_EXPERTS = 5

StanceV4 = Literal["support", "oppose", "cautious", "neutral"]


def normalize_legacy_stance(value: str) -> str:
    """旧枚举归一化（for→support / against→oppose），V4 契约层统一收口。"""
    v = value.strip().lower() if isinstance(value, str) else value
    if v == "for":
        return "support"
    if v == "against":
        return "oppose"
    return value


# ============================================================
# 主 Agent 契约（spec §7）
# ============================================================

class DecisionModel(BaseModel):
    """Canonical Decision State 的决策建模部分（spec §7.2）。

    用户确认事实（verified_user_facts）属于 Canonical State，
    任何 Agent 不得直接覆盖（Reducer 强制，Phase 后续实现）。
    """

    question: str = Field(min_length=4, max_length=500)
    options: list[str] = Field(default_factory=list, max_length=8)
    user_goals: list[str] = Field(default_factory=list, max_length=8)
    constraints: list[str] = Field(default_factory=list, max_length=12)
    verified_user_facts: list[str] = Field(default_factory=list, max_length=30)
    preferences: list[str] = Field(default_factory=list, max_length=12)
    unknowns: list[str] = Field(default_factory=list, max_length=12)
    decision_deadline: datetime | None = None
    risk_level: Literal["low", "medium", "high"] = "medium"


class BudgetPolicy(BaseModel):
    """防无限循环默认预算（spec §7.6）——默认值，必须配置化并记录实际用量。"""

    max_main_actions: int = Field(default=12, ge=1)
    max_replans: int = Field(default=3, ge=0)
    max_search_rounds_per_gap: int = Field(default=2, ge=1)
    max_debate_rounds_per_dispute: int = Field(default=2, ge=1)
    max_task_retries: int = Field(default=2, ge=0)
    max_consecutive_idle_turns: int = Field(default=2, ge=1)
    # Audit 与 Finalize 预算必须预留（spec §16.1），前面阶段不许吃光
    audit_reserve_ratio: float = Field(default=0.15, gt=0, le=0.5)
    synthesis_reserve_ratio: float = Field(default=0.15, gt=0, le=0.5)

    @model_validator(mode="after")
    def reserves_leave_room(self) -> "BudgetPolicy":
        if self.audit_reserve_ratio + self.synthesis_reserve_ratio >= 0.6:
            raise ValueError("audit + synthesis 预留比例过高，专家阶段将被挤死")
        return self


class PlanSpec(BaseModel):
    """主 Agent 计划（spec §7.3）。历史版本必须保留（plan_versions 表，Phase 1）。"""

    plan_version: int = Field(ge=1)
    objective: str = Field(min_length=8, max_length=300)
    research_tasks: list[str] = Field(default_factory=list, max_length=20)
    expert_tasks: list[str] = Field(default_factory=list, max_length=MAX_EXPERTS)
    expected_conflicts: list[str] = Field(default_factory=list, max_length=10)
    blocking_unknowns: list[str] = Field(default_factory=list, max_length=10)
    non_blocking_unknowns: list[str] = Field(default_factory=list, max_length=10)
    stop_conditions: list[str] = Field(default_factory=list, max_length=10)
    budget_allocation: dict[str, Any] = Field(default_factory=dict)
    created_from_state_version: int = Field(ge=1)


class MainAgentActionRequest(BaseModel):
    """主 Agent 每轮的动作请求信封。payload 按动作类型由上层 Workflow 校验。"""

    action: MainAgentAction
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("action", mode="before")
    @classmethod
    def _reject_unknown_action(cls, value):
        if isinstance(value, str) and value not in {a.value for a in MainAgentAction}:
            raise ValueError(f"未知主 Agent 动作: {value!r}")
        return value


# ============================================================
# 子 Agent 契约（spec §8）
# ============================================================

class ExpertTaskPacket(BaseModel):
    """专家任务包（spec §8.2）。禁止"请分析这个问题"式空任务。"""

    task_id: str = Field(min_length=3, max_length=64)
    expert_id: str
    objective: str = Field(min_length=10, max_length=500)
    decision_options: list[str] = Field(default_factory=list, max_length=8)
    relevant_user_facts: list[str] = Field(default_factory=list, max_length=20)
    relevant_evidence: list[str] = Field(default_factory=list, max_length=20)
    known_unknowns: list[str] = Field(default_factory=list, max_length=10)
    role_contract: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list, max_length=10)
    forbidden_actions: list[str] = Field(default_factory=list, max_length=10)
    expected_output: dict[str, Any] = Field(default_factory=dict)
    deadline_at: datetime | None = None
    budget: dict[str, Any] = Field(default_factory=dict)
    context_version: int = Field(ge=1)

    @field_validator("expert_id")
    @classmethod
    def _known_expert(cls, value):
        if value not in EXPERT_IDS:
            raise ValueError(f"未知专家 ID: {value!r}（八人库之外）")
        return value

    @field_validator("objective")
    @classmethod
    def _objective_must_be_specific(cls, value):
        stripped = value.strip()
        if len(stripped) < 10:
            raise ValueError("任务目标过短：必须是具体专业问题，不是泛泛的'请分析'")
        vague_markers = ("请分析这个问题", "分析一下", "看看这个问题的各个方面")
        if any(marker in stripped for marker in vague_markers) and len(stripped) < 25:
            raise ValueError("任务目标过于泛化：禁止只发送'请分析这个问题'")
        return stripped


class SubAgentCompletion(BaseModel):
    objective_answered: bool = False
    evidence_checked: bool = False
    boundary_checked: bool = False


class SubAgentResult(BaseModel):
    """子 Agent 结构化输出（spec §8.3，ExpertResearchResult 的 V4 升级）。

    status:
      completed —— 正常完成
      partial   —— 预算退出，带安全有限结论（不得抛异常毒化全局）
      silent    —— 相关性过低不发言（spec §4.4）
      failed    —— 仅记录，由主 Agent 决定重试/降级/替换
    """

    task_id: str
    agent_id: str
    status: Literal["completed", "partial", "silent", "failed"] = "completed"
    relevance: float = Field(default=0.7, ge=0.0, le=1.0)
    stance: StanceV4 = "neutral"
    judgment: str = Field(default="", max_length=500)
    analysis_points: list[str] = Field(default_factory=list, max_length=3)
    evidence_refs: list[str] = Field(default_factory=list, max_length=5)
    assumptions: list[str] = Field(default_factory=list, max_length=5)
    unknowns: list[str] = Field(default_factory=list, max_length=5)
    action: str = Field(default="", max_length=300)
    reversal_conditions: list[str] = Field(default_factory=list, max_length=3)
    questions_for_other_agents: list[str] = Field(default_factory=list, max_length=3)
    questions_for_user: list[str] = Field(default_factory=list, max_length=2)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    completion: SubAgentCompletion = Field(default_factory=SubAgentCompletion)
    # 统一数字账本条目：{label, value, basis: user_input|assumption|calculation|evidence, note}
    # 关键数字（收入/利率/月供/机会成本/租金…）必须报数——跨 Agent 引用同一本账
    numbers: list[dict[str, Any]] = Field(default_factory=list, max_length=10)

    @field_validator("stance", mode="before")
    @classmethod
    def _normalize_stance(cls, value):
        return normalize_legacy_stance(value)

    @model_validator(mode="after")
    def _silent_must_not_fabricate(self) -> "SubAgentResult":
        if self.status == "silent":
            if self.judgment.strip() and len(self.analysis_points) > 0:
                raise ValueError("silent 结果不得携带观点（spec §10.1 不生成伪观点）")
            if len(self.questions_for_user) > 1:
                raise ValueError("silent 结果至多一个问题")
        return self


# ============================================================
# 辩论契约（spec §9）
# ============================================================

class DisputeIssue(BaseModel):
    """实质分歧（spec §9.2）。只围绕会影响建议的分歧开辩论。"""

    dispute_id: str = Field(min_length=3, max_length=64)
    question: str = Field(min_length=8, max_length=300)
    importance: float = Field(ge=0.0, le=1.0)
    conflict_type: ConflictType
    claims: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    participants: list[str] = Field(max_length=MAX_EXPERTS)
    shared_evidence_refs: list[str] = Field(default_factory=list, max_length=10)
    missing_variable: str | None = None
    status: Literal["open", "narrowed", "resolved", "retained"] = "open"
    round: int = Field(default=0, ge=0)

    @field_validator("participants")
    @classmethod
    def _participants_valid(cls, value):
        if len(value) < 2:
            raise ValueError("争议至少需要两位参与者")
        unknown = [p for p in value if p not in EXPERT_IDS]
        if unknown:
            raise ValueError(f"争议参与者必须是八人库专家: {unknown}")
        if len(set(value)) != len(value):
            raise ValueError("参与者不得重复")
        return value

    @field_validator("importance")
    @classmethod
    def _low_importance_not_worth_debate(cls, value):
        # spec §9.1：辩论只围绕会影响建议的实质分歧。阈值保守放在 0.3，
        # 主 Agent 提取时可用更高阈值，但契约层拒绝明显不重要的争议。
        if value < 0.3:
            raise ValueError("importance < 0.3 的分歧不值得开辩论轮")
        return value


class DebateTurnResponse(BaseModel):
    """被质询专家的每轮回应（spec §9.3）。"""

    dispute_id: str
    agent_id: str
    position_before: StanceV4 | Literal["defer", "conditional_support"]
    position_after: StanceV4 | Literal["defer", "conditional_support"]
    changed: bool = False
    reason: str = Field(default="", max_length=500)
    new_evidence_refs: list[str] = Field(default_factory=list, max_length=5)
    remaining_disagreement: str = Field(default="", max_length=300)
    needs_user_input: bool = False

    @field_validator("position_before", "position_after", mode="before")
    @classmethod
    def _normalize(cls, value):
        return normalize_legacy_stance(value)

    @model_validator(mode="after")
    def _changed_consistency(self) -> "DebateTurnResponse":
        if self.changed and self.position_before == self.position_after:
            raise ValueError("changed=true 但立场未变化")
        if not self.changed and self.position_before != self.position_after:
            raise ValueError("立场变化必须显式 changed=true 并给出 reason")
        return self


# ============================================================
# Context 契约（spec §11）
# ============================================================

class ContextPacket(BaseModel):
    """Context Compiler 的输出（spec §11.2）。

    每个 Packet 带 context_version 和内容摘要哈希；
    子 Agent 写回结构化 Delta（SubAgentResult），不能直接改 Canonical State。
    """

    task_id: str
    compiled_for: str  # agent_id 或 "main"
    context_version: int = Field(ge=1)
    content_sha256: str = Field(min_length=8, max_length=64)
    sections: dict[str, Any] = Field(default_factory=dict)


class AgentMessage(BaseModel):
    """主 Agent 与子 Agent 的结构化交互记录（spec §10.2）。

    不直接拼接进全局 transcript——前端叙事由 event_bridge 生成。
    """

    message_id: str
    kind: Literal["task", "followup", "debate", "audit", "user", "system"]
    from_agent: str
    to_agent: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


# ============================================================
# Evidence 契约（spec §12 / §14.4）
# ============================================================

class EvidenceRecordV4(BaseModel):
    """共享 Evidence Kernel 的记录结构。"""

    id: str = Field(min_length=3, max_length=64)
    claim_id: str = Field(min_length=1, max_length=64)
    kind: EvidenceKind
    status: EvidenceStatus = EvidenceStatus.UNVERIFIED
    source_title: str = Field(default="", max_length=200)
    source_url: str | None = None
    source_domain: str = Field(default="", max_length=100)
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    excerpt: str = Field(default="", max_length=2000)
    relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    reliability: float = Field(default=0.5, ge=0.0, le=1.0)
    authority_level: Literal[
        "official", "authoritative", "professional", "media", "community", "unknown"
    ] = "unknown"
    supports_claim: str = Field(default="", max_length=500)
    contradicts_claim: str = Field(default="", max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _web_requires_url(self) -> "EvidenceRecordV4":
        if self.kind == EvidenceKind.WEB_SOURCE and not self.source_url:
            raise ValueError("web_source 证据必须有 source_url（spec §21.1）")
        return self


# ============================================================
# 最终输出契约（spec §18）
# ============================================================

class Recommendation(BaseModel):
    option: str = Field(min_length=1, max_length=200)
    strength: Literal["conditional", "clear", "insufficient"]
    summary: str = Field(min_length=1, max_length=2000)
    applicable_conditions: list[str] = Field(default_factory=list, max_length=8)


class CompletionState(BaseModel):
    status: RunExitStatus
    limitations: list[str] = Field(default_factory=list, max_length=8)


class FinalReport(BaseModel):
    """最终输出契约（spec §18）。文案层可重组，结构化事实不得被改写。"""

    recommendation: Recommendation
    decision_model: dict[str, Any] = Field(default_factory=dict)
    option_comparison: list[dict[str, Any]] = Field(default_factory=list)
    expert_findings: list[dict[str, Any]] = Field(default_factory=list)
    resolved_disputes: list[dict[str, Any]] = Field(default_factory=list)
    remaining_disagreements: list[dict[str, Any]] = Field(default_factory=list)
    key_evidence: list[str] = Field(default_factory=list, max_length=20)
    remaining_uncertainties: list[str] = Field(default_factory=list, max_length=10)
    action_plan: list[str] = Field(default_factory=list, max_length=10)
    risk_controls: list[str] = Field(default_factory=list, max_length=10)
    reversal_conditions: list[str] = Field(default_factory=list, max_length=10)
    completion: CompletionState
    # 主席统一结论卡（300-500 字）：建议一句话 + 已确认/未确认 + 推进条件 + 补数清单
    chair_summary: str = Field(default="", max_length=1200)

    @model_validator(mode="after")
    def _insufficient_needs_uncertainty(self) -> "FinalReport":
        if (
            self.recommendation.strength == "insufficient"
            and not self.remaining_uncertainties
        ):
            raise ValueError("strength=insufficient 必须列出 remaining_uncertainties")
        return self
