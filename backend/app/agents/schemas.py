"""Schemas shared by the expert ReAct graph, harness, and public trace UI."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field, model_validator


AgentAction = Literal["tool", "finish"]
EvidenceKind = Literal["user_fact", "web_source", "calculation", "inference"]
BasisLevel = Literal["limited", "moderate", "strong"]
DecisionPosition = Literal[
    "support",
    "oppose",
    "defer",
    "conditional_support",
    "undetermined",
]
AgentStatus = Literal[
    "idle",
    "running",
    "finalizing",
    "completed",
    "degraded",
    "failed",
    "cancelled",
]
PublicIntent = Literal[
    "verify_public_fact",
    "find_comparable_case",
    "quantify_tradeoff",
    "review_user_history",
    "fill_evidence_gap",
]
TracePhase = Literal[
    "started",
    "deciding",
    "tool_started",
    "tool_completed",
    "evidence_added",
    "finalizing",
    "completed",
    "degraded",
    "failed",
]


class SourcePreview(BaseModel):
    title: str
    domain: str = ""
    url: str | None = None


class ToolResultItem(BaseModel):
    title: str
    url: str | None = None
    content: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolObservation(BaseModel):
    tool_call_id: str
    tool_name: str
    ok: bool
    results: list[ToolResultItem] = Field(default_factory=list, max_length=5)
    error_code: str | None = None
    public_error: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    cache_hit: bool = False


class EvidenceRecord(BaseModel):
    evidence_id: str
    agent_id: str
    tool_call_id: str
    claim_summary: str
    source_title: str
    source_url: str | None = None
    source_domain: str = ""
    source_excerpt: str = ""
    retrieved_at: float
    relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    reliability: float = Field(default=0.5, ge=0.0, le=1.0)
    strength: Literal["high", "medium", "low"] = "medium"
    kind: EvidenceKind = "inference"
    supports_claim: str = ""
    published_at: str | None = None
    freshness_note: str | None = None
    calculation_inputs: dict[str, Any] = Field(default_factory=dict)
    calculation_formula: str | None = None
    assumptions: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def kind_requires_auditable_fields(self) -> "EvidenceRecord":
        if self.kind == "web_source" and not self.source_url:
            raise ValueError("web_source evidence requires source_url")
        if self.kind == "calculation":
            if not self.calculation_inputs or not self.calculation_formula:
                raise ValueError(
                    "calculation evidence requires calculation_inputs and calculation_formula"
                )
        return self


Stance = Literal["support", "oppose", "cautious", "neutral"]


class ExpertResearchResult(BaseModel):
    """专家公开回复契约（spec v1.0 §4.3）。

    stance: support(推进)/oppose(反对)/cautious(谨慎推进)/neutral(暂不判断)
    judgment: 20-80 字当前倾向
    analysis_points: 1-3 条角色视角分析，每条 40-120 字
    action: 最多一个当前优先行动（可空）
    key_question: 最多一个可能改变判断的问题（可空）
    relevance/should_speak: 相关性低于 0.35 时本角色不发言
    """
    agent_id: str
    relevance: float = Field(default=0.7, ge=0.0, le=1.0)
    should_speak: bool = True
    stance: Stance
    confidence: float = Field(ge=0.0, le=1.0)
    judgment: str = Field(min_length=1, max_length=200)
    lens_title: str = ""
    analysis_points: list[str] = Field(default_factory=list, max_length=3)
    action: str = Field(default="", max_length=300)
    key_question: str = Field(default="", max_length=200)
    reversal_condition: str = Field(default="", max_length=300)
    evidence_refs: list[str] = Field(default_factory=list, max_length=3)
    assumptions: list[str] = Field(default_factory=list, max_length=5)
    tool_calls_used: int = Field(default=0, ge=0)
    degraded: bool = False
    degradation_reason: str | None = None

    @model_validator(mode="after")
    def degraded_result_has_reason(self) -> "ExpertResearchResult":
        if self.degraded and not self.degradation_reason:
            raise ValueError("degraded result must include degradation_reason")
        return self

    @model_validator(mode="after")
    def silent_result_is_empty(self) -> "ExpertResearchResult":
        """spec §10.1：should_speak=false 时不得生成伪观点。"""
        if not self.should_speak:
            if self.judgment.strip():
                # spec §10.1: silent 时 judgment 只允许极短内部标记（≤20 字）
                if len(self.judgment) > 20 or len(self.analysis_points) > 0:
                    raise ValueError(
                        "should_speak=false 时不得生成观点（judgment≤20字且无 analysis_points）"
                    )
        return self


class ToolRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    public_intent: PublicIntent


class AgentDecision(BaseModel):
    action: AgentAction
    tool_request: ToolRequest | None = None
    result: ExpertResearchResult | None = None

    @model_validator(mode="before")
    @classmethod
    def repair_envelope(cls, data: Any) -> Any:
        """修复 GLM 结构化输出的两类真实事故（议会 45577003 心理学家案）。

        1. 模型把 ExpertResearchResult 字段（judgment/lens_title/...）直接平铺在
           顶层、完全跳过 AgentDecision 外壳 → 把整包包成 {"action":"finish","result":...}。
           不修则 Pydantic literal_error 让整个专家 should_speak=false 沉默，
           尽管内容本身质量合格。
        2. action 被填成说明句而非 'tool'/'finish' → 按载荷推断真实意图：
           有 tool_request → tool；有 result 或结论字段 → finish。

        wrap 时对 result 必填字段兜底（agent_id 空/stance neutral/confidence 低）：
        比整包丢弃好——内容是模型真实产出的分析，丢了就是"专家沉默"。
        """
        if not isinstance(data, dict):
            return data
        action = data.get("action")
        if action in ("tool", "finish"):
            return data
        if data.get("tool_request") and not data.get("result"):
            return {**data, "action": "tool"}
        if "result" in data:
            return {**data, "action": "finish"}
        if any(k in data for k in ("judgment", "analysis_points", "lens_title")):
            payload = {k: v for k, v in data.items() if k != "action"}
            payload.setdefault("agent_id", "")
            payload.setdefault("stance", "neutral")
            payload.setdefault("confidence", 0.3)
            return {"action": "finish", "result": payload}
        return data

    @model_validator(mode="after")
    def action_payload_is_exclusive(self) -> "AgentDecision":
        if self.action == "tool":
            if self.tool_request is None or self.result is not None:
                raise ValueError("tool action requires tool_request only")
        elif self.result is None or self.tool_request is not None:
            raise ValueError("finish action requires result only")
        return self


class AgentTraceEvent(BaseModel):
    trace_id: str
    run_id: str
    agent_id: str
    step: int = Field(ge=0)
    phase: TracePhase
    public_message: str
    public_intent: PublicIntent | None = None
    tool_name: str | None = None
    display_query: str | None = None
    source_previews: list[SourcePreview] = Field(default_factory=list, max_length=5)
    evidence_count: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    cache_hit: bool | None = None
    timestamp: float


class SafeActionRecord(BaseModel):
    step: int
    action: str
    tool_name: str | None = None
    arguments_summary: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    duration_ms: int = 0
    timestamp: float


class ExpertAgentState(TypedDict, total=False):
    run_id: str
    parliament_id: str
    agent_id: str
    expert_profile_id: str
    goal: str
    user_brief: dict[str, Any]
    category: str
    messages: list[Any]
    observations: list[dict[str, Any]]
    evidence_ledger: list[dict[str, Any]]
    safe_action_history: list[dict[str, Any]]
    reason_step_count: int
    tool_call_count: int
    started_at: float
    deadline_at: float
    status: AgentStatus
    last_error: str | None
    decision: dict[str, Any] | None
    pending_observation: dict[str, Any] | None
    final_result: dict[str, Any] | None
    metrics: dict[str, Any]
