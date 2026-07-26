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


class ExpertResearchResult(BaseModel):
    agent_id: str
    stance: Literal["for", "against", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    position: DecisionPosition = "undetermined"
    basis_level: BasisLevel = "limited"
    thesis: str = Field(min_length=1)
    key_risks: list[str] = Field(default_factory=list, max_length=5)
    evidence_refs: list[str] = Field(default_factory=list, max_length=3)
    assumptions: list[str] = Field(default_factory=list, max_length=5)
    unknowns: list[str] = Field(default_factory=list, max_length=5)
    reversal_conditions: list[str] = Field(min_length=1, max_length=5)
    next_actions: list[str] = Field(default_factory=list, max_length=5)
    tool_calls_used: int = Field(default=0, ge=0)
    degraded: bool = False
    degradation_reason: str | None = None

    @model_validator(mode="after")
    def degraded_result_has_reason(self) -> "ExpertResearchResult":
        if self.degraded and not self.degradation_reason:
            raise ValueError("degraded result must include degradation_reason")
        return self


class ToolRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    public_intent: PublicIntent


class AgentDecision(BaseModel):
    action: AgentAction
    tool_request: ToolRequest | None = None
    result: ExpertResearchResult | None = None

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
