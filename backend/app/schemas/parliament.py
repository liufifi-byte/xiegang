"""议会 HTTP 请求 / 响应 schemas。"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class CreateParliamentRequest(BaseModel):
    topic: str
    topic_category: str | None = None
    user_background: str | None = None
    user_id: str | None = None


class ParliamentResponse(BaseModel):
    id: str
    topic: str
    status: str
    created_at: datetime
    finished_at: datetime | None = None
    summary: dict[str, Any] | None = None
    transcript: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
