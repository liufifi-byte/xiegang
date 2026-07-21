"""Public, privacy-safe observability for expert agent runs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.agents.profiles import ExpertProfile
from app.agents.schemas import AgentTraceEvent, SourcePreview, ToolObservation


class TraceRenderer:
    """Render structured lifecycle events without using model reasoning text."""

    @staticmethod
    def message(
        phase: str,
        profile: ExpertProfile,
        *,
        tool_name: str | None = None,
        result_count: int = 0,
        evidence_count: int = 0,
        error: str | None = None,
    ) -> str:
        name = profile.display_name
        if phase == "started":
            return f"{name}开始自主调研"
        if phase == "deciding":
            return f"{name}正在判断信息缺口"
        if phase == "tool_started":
            labels = {
                "web_search": "正在核实公开信息",
                "calculator": "正在量化选项差异",
                "memory_search": "正在回顾相关历史决策",
            }
            return labels.get(tool_name or "", "正在使用研究工具")
        if phase == "tool_completed":
            if error:
                return error
            return f"工具完成，获得 {result_count} 项结果"
        if phase == "evidence_added":
            return f"已保留 {evidence_count} 条可追溯证据"
        if phase == "finalizing":
            return f"{name}正在整合证据并形成判断"
        if phase == "completed":
            return f"{name}完成自主调研"
        if phase == "degraded":
            return f"{name}基于有限信息完成分析"
        return f"{name}调研未完成"


class AgentTraceSink:
    def __init__(
        self,
        parliament_id: str,
        writer: Callable[[str, str, dict], int] | None = None,
    ):
        self.parliament_id = parliament_id
        if writer is None:
            from app.event_store import append_event

            writer = append_event
        self.writer = writer
        self.events: list[AgentTraceEvent] = []

    async def emit(self, event: AgentTraceEvent) -> None:
        self.events.append(event)
        try:
            await asyncio.to_thread(
                self.writer,
                self.parliament_id,
                "agent_trace",
                event.model_dump(mode="json"),
            )
        except Exception:
            # Public observability is best effort; it cannot take down the agent.
            return


def source_previews(observation: ToolObservation) -> list[SourcePreview]:
    previews: list[SourcePreview] = []
    for item in observation.results[:5]:
        domain = ""
        if item.url:
            from urllib.parse import urlparse

            domain = urlparse(item.url).netloc
        previews.append(SourcePreview(title=item.title, domain=domain, url=item.url))
    return previews
