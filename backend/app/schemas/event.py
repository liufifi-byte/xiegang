"""SSE 事件类型 schemas。"""
from typing import Any, Literal

from pydantic import BaseModel


EventType = Literal[
    "parliament_started",
    "node_complete",
    "member_speaking",
    "tool_call",
    "agent_trace",
    "round_end",
    "election_phase",
    "chair_elected",
    "summary_ready",
    "parliament_done",
    "cancelled",
    "error",
]


class ParliamentEvent(BaseModel):
    type: EventType
    parliament_id: str
    payload: dict[str, Any] = {}
