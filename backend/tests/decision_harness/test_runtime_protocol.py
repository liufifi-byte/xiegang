"""Runtime 协议、Fake 实现与模式开关测试（spec §13.2 / §19.4 / §24.5）。"""
import asyncio

import pytest

from app.decision_harness.runtime import (
    AgentRuntime,
    AgentRuntimeError,
    FakeAgentRuntime,
    RuntimeEvent,
    get_runtime_mode,
)


def _scripted_runtime() -> FakeAgentRuntime:
    return FakeAgentRuntime(
        scripts={
            "session_root": [
                [
                    RuntimeEvent(seq=1, type="turn_started", payload={"prompt_len": 10}),
                    RuntimeEvent(seq=2, type="agent_action", payload={"action": "revise_plan"}),
                ],
                [RuntimeEvent(seq=3, type="turn_started")],
            ]
        }
    )


def test_runtime_mode_defaults_to_legacy():
    # conftest 不注入 DECISION_RUNTIME_MODE → 默认必须 legacy（spec §24.4）
    assert get_runtime_mode() == "legacy"


def test_runtime_mode_rejects_invalid(monkeypatch):
    from app.config import get_settings

    class _FakeSettings:
        decision_runtime_mode = "yolo"

    monkeypatch.setattr(
        "app.decision_harness.runtime.get_settings", lambda: _FakeSettings()
    )
    with pytest.raises(AgentRuntimeError, match="invalid_runtime_mode"):
        get_runtime_mode()


def test_fake_runtime_implements_protocol():
    assert isinstance(FakeAgentRuntime(), AgentRuntime)


def test_fake_runtime_full_lifecycle():
    runtime = _scripted_runtime()

    async def run():
        await runtime.start_session("session_root", instructions="你是决策议长")
        collected = []
        async for event in runtime.start_turn("session_root", "规划一下"):
            collected.append(event.type)
        assert collected == ["turn_started", "agent_action"]
        # 第二轮（脚本只剩一个事件）
        async for event in runtime.start_turn("session_root", "继续"):
            collected.append(event.type)
        assert collected == ["turn_started", "agent_action", "turn_started"]
        # 第三轮无脚本 → 空轮
        empty = [e async for e in runtime.start_turn("session_root", "再跑")]
        assert empty == []
        assert runtime.turn_count("session_root") == 3
        await runtime.close_session("session_root")
        assert runtime.session_state("session_root") is None

    asyncio.run(run())
    assert ("start_session", "session_root") in runtime.calls


def test_fake_runtime_resume_and_not_found():
    runtime = _scripted_runtime()

    async def run():
        await runtime.start_session("s", instructions="x")
        await runtime.resume_session("s")
        assert runtime.session_state("s")["resumed"] is True
        with pytest.raises(AgentRuntimeError, match="session_not_found"):
            await runtime.resume_session("missing")

    asyncio.run(run())


def test_fake_runtime_interrupt_mid_turn():
    long_turn = [[RuntimeEvent(seq=i, type="step") for i in range(6)]]
    runtime = FakeAgentRuntime(scripts={"s": long_turn}, turn_delay_seconds=0.05)

    async def _collect(agen):
        out = []
        async for e in agen:
            out.append(e.type)
        return out

    async def run():
        await runtime.start_session("s", instructions="x")
        task = asyncio.create_task(_collect(runtime.start_turn("s", "long turn")))
        await asyncio.sleep(0.08)
        await runtime.interrupt_turn("s")
        events = await task
        assert events[-1] == "turn_interrupted"

    asyncio.run(run())


def test_fake_runtime_turn_requires_started_session():
    runtime = _scripted_runtime()

    async def run():
        with pytest.raises(AgentRuntimeError, match="未启动"):
            async for _ in runtime.start_turn("ghost", "hi"):
                pass

    asyncio.run(run())
