"""议会 HTTP 请求 / 响应 schemas。"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.agents.profiles import EXPERT_PROFILES


class AnswerPayload(BaseModel):
    """决策路径答案项, 复用 schemas.decision_path.AnswerItem 但避免循环引用"""

    question_id: str
    value: str | list[str]
    custom_value: str | None = None


class CreateParliamentRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    topic_category: str | None = None
    user_background: str | None = Field(default=None, max_length=4000)
    user_id: str | None = None
    selected_expert_ids: list[str] | None = None
    # PRD §13.4: 决策路径 bundle(可选, 老客户端不带也能创建会议)
    idempotency_key: str | None = Field(default=None, max_length=128)
    decision_path_generation_id: str | None = Field(default=None, max_length=128)
    brief_confirmation: str | None = None
    decision_path_answers: list[AnswerPayload] = Field(default_factory=list)

    @field_validator("selected_expert_ids")
    @classmethod
    def validate_selected_experts(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        if value is None:
            return None
        if not 3 <= len(value) <= 5:
            raise ValueError("selected_expert_ids must contain 3 to 5 experts")
        if len(set(value)) != len(value):
            raise ValueError("selected_expert_ids must not contain duplicates")
        unknown = [expert_id for expert_id in value if expert_id not in EXPERT_PROFILES]
        if unknown:
            raise ValueError(f"unknown expert ids: {', '.join(unknown)}")
        return value


class ParliamentResponse(BaseModel):
    id: str
    topic: str
    status: str
    created_at: datetime
    finished_at: datetime | None = None
    summary: dict[str, Any] | None = None
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    members_state: dict[str, Any] = Field(default_factory=dict)
    selected_expert_ids: list[str] = Field(default_factory=list)
    agent_results: dict[str, Any] = Field(default_factory=dict)
    agent_traces: list[dict[str, Any]] = Field(default_factory=list)
    current_phase: str = "queued"
    worker_available: bool = False
    completed_expert_count: int = 0
    clarification_status: str = "not_checked"
    clarification_questions: list[dict[str, Any]] = Field(default_factory=list)
    clarification_answers: list[dict[str, Any]] = Field(default_factory=list)


class ClarificationAnswer(BaseModel):
    question_id: str = Field(min_length=2, max_length=64)
    value: str | float | int


class SubmitClarificationRequest(BaseModel):
    action: str = Field(pattern="^(answer|skip)$")
    answers: list[ClarificationAnswer] = Field(default_factory=list, max_length=3)
