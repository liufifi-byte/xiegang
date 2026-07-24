"""议会 HTTP 请求 / 响应 schemas。"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.agents.profiles import EXPERT_PROFILES


class CreateParliamentRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    topic_category: str | None = None
    user_background: str | None = Field(default=None, max_length=4000)
    user_id: str | None = None
    selected_expert_ids: list[str] | None = None

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
