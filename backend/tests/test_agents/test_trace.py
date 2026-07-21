import pytest

from app.agents.profiles import get_expert_profile
from app.agents.schemas import AgentTraceEvent
from app.agents.trace import AgentTraceSink, TraceRenderer


@pytest.mark.asyncio
async def test_trace_sink_is_best_effort_and_keeps_event():
    def broken_writer(*args):
        raise RuntimeError("db offline")

    sink = AgentTraceSink("p1", writer=broken_writer)
    event = AgentTraceEvent(
        trace_id="t1",
        run_id="r1",
        agent_id="economist",
        step=0,
        phase="started",
        public_message="经济学家开始自主调研",
        timestamp=1.0,
    )
    await sink.emit(event)
    assert sink.events == [event]


def test_renderer_uses_templates_not_model_reasoning():
    profile = get_expert_profile("economist")
    assert TraceRenderer.message("deciding", profile) == "经济学家正在判断信息缺口"
    assert "公开信息" in TraceRenderer.message(
        "tool_started", profile, tool_name="web_search"
    )
