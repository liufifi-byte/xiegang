"""Runtime Adapter 协议与 Fake 实现（spec 2026-08-22 §13.2）。

业务代码不得直接依赖 Codex SDK 类型——只依赖本模块的 AgentRuntime 协议。
Phase 0 提供 FakeAgentRuntime 使新功能全部可离线测试；
CodexAgentRuntime（Phase 1）与 LegacyGlmAgentRuntime 后续在此目录补充。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol, runtime_checkable

from app.config import get_settings

RuntimeMode = Literal["legacy", "shadow", "codex_harness"]
VALID_RUNTIME_MODES: tuple[str, ...] = ("legacy", "shadow", "codex_harness")


class AgentRuntimeError(Exception):
    """Runtime 层错误先转领域错误码，再由上层生成用户文案（spec §24.10）。"""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


def get_runtime_mode() -> RuntimeMode:
    """读取并校验 DECISION_RUNTIME_MODE。默认 legacy（spec §19.4）。"""
    raw = get_settings().decision_runtime_mode
    if raw not in VALID_RUNTIME_MODES:
        raise AgentRuntimeError(
            "invalid_runtime_mode",
            f"DECISION_RUNTIME_MODE={raw!r} 非法，可选: {VALID_RUNTIME_MODES}",
        )
    return raw  # type: ignore[return-value]


@dataclass
class RuntimeEvent:
    """Runtime 事件的最小中立表示。

    这是产品内部协议——Codex 原始事件必须先经 event_bridge 转换，
    前端只依赖产品事件（spec §24.9）。
    """

    seq: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    thread_id: str | None = None


@runtime_checkable
class AgentRuntime(Protocol):
    """统一 Runtime 接口（spec §13.2）。"""

    async def start_session(self, session_id: str, *, instructions: str) -> None: ...

    async def resume_session(
        self, session_id: str, *, runtime_thread_id: str | None = None
    ) -> None: ...

    async def start_turn(
        self, session_id: str, prompt: str, *, tools: list[str] | None = None
    ) -> AsyncIterator[RuntimeEvent]: ...

    async def interrupt_turn(self, session_id: str) -> None: ...

    async def compact_session(self, session_id: str) -> None: ...

    async def close_session(self, session_id: str) -> None: ...


class FakeAgentRuntime:
    """确定性脚本化 Runtime——新功能必须可离线测试（spec §24.5）。

    用法：构造时注入 script={session_id: [turn_events, ...]}；
    start_turn 依次弹出每一轮的事件序列。未注入脚本的会话返回空轮。
    生命周期状态机用于测试 Adapter 的恢复/取消路径。
    """

    def __init__(
        self,
        scripts: dict[str, list[list[RuntimeEvent]]] | None = None,
        turn_delay_seconds: float = 0.0,
    ):
        self._scripts = scripts or {}
        self._turn_index: dict[str, int] = {}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._live_turns: dict[str, bool] = {}
        self._event_seq = 0
        self._delay = turn_delay_seconds
        # 观测钩子（测试断言用）
        self.calls: list[tuple[str, str]] = []

    # ---- 生命周期 ----

    async def start_session(self, session_id: str, *, instructions: str) -> None:
        self.calls.append(("start_session", session_id))
        if session_id in self._sessions:
            raise AgentRuntimeError("session_exists", f"会话 {session_id} 已存在")
        self._sessions[session_id] = {
            "instructions": instructions,
            "resumed": False,
            "compacted": 0,
        }
        self._turn_index[session_id] = 0

    async def resume_session(
        self, session_id: str, *, runtime_thread_id: str | None = None
    ) -> None:
        self.calls.append(("resume_session", session_id))
        if session_id not in self._sessions:
            raise AgentRuntimeError(
                "session_not_found",
                f"会话 {session_id} 不存在，无法恢复",
                retryable=False,
            )
        self._sessions[session_id]["resumed"] = True
        self._sessions[session_id]["resumed_thread_id"] = runtime_thread_id

    async def interrupt_turn(self, session_id: str) -> None:
        self.calls.append(("interrupt_turn", session_id))
        self._live_turns[session_id] = False

    async def compact_session(self, session_id: str) -> None:
        self.calls.append(("compact_session", session_id))
        if session_id not in self._sessions:
            raise AgentRuntimeError("session_not_found", f"会话 {session_id} 不存在")
        self._sessions[session_id]["compacted"] += 1

    async def close_session(self, session_id: str) -> None:
        self.calls.append(("close_session", session_id))
        self._sessions.pop(session_id, None)
        self._turn_index.pop(session_id, None)
        self._live_turns.pop(session_id, None)

    # ---- 轮次 ----

    async def start_turn(
        self, session_id: str, prompt: str, *, tools: list[str] | None = None
    ) -> AsyncIterator[RuntimeEvent]:
        self.calls.append(("start_turn", session_id))
        if session_id not in self._sessions:
            raise AgentRuntimeError(
                "session_not_found", f"会话 {session_id} 未启动", retryable=True
            )
        self._live_turns[session_id] = True
        idx = self._turn_index.get(session_id, 0)
        turns = self._scripts.get(session_id, [])
        events = turns[idx] if idx < len(turns) else []
        self._turn_index[session_id] = idx + 1
        for event in events:
            if self._delay:
                await asyncio.sleep(self._delay)
            if not self._live_turns.get(session_id, True):
                # 模拟被 interrupt 的轮：发一个中断事件后终止本轮
                self._event_seq += 1
                yield RuntimeEvent(
                    seq=self._event_seq, type="turn_interrupted", thread_id=session_id
                )
                return
            self._event_seq += 1
            yield RuntimeEvent(
                seq=self._event_seq,
                type=event.type,
                payload=dict(event.payload),
                thread_id=session_id,
            )
        self._live_turns[session_id] = False

    # ---- 测试辅助 ----

    def session_state(self, session_id: str) -> dict[str, Any] | None:
        return self._sessions.get(session_id)

    def turn_count(self, session_id: str) -> int:
        return self._turn_index.get(session_id, 0)
