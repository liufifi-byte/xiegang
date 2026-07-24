"""Schemas for the pre-meeting expert recommendation preview."""

from pydantic import BaseModel, Field


class ExpertSelectionPreviewRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    topic_category: str | None = None
    user_background: str | None = Field(default=None, max_length=4000)


class ExpertSelectionPreviewResponse(BaseModel):
    recommended_expert_ids: list[str]
    coverage: list[str]
    reasons: dict[str, str]
    selection_source: str
