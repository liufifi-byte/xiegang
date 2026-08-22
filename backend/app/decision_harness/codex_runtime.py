"""Codex App Server Runtime Adapter（spec 2026-08-22 §13.2，Phase 1）。

把 openai-codex SDK（本地 App Server）适配成 AgentRuntime 协议。
- SDK 是可选依赖（pyproject [codex] extra）：缺失时构造抛 AgentRuntimeError，
  绝不让 import 错误穿透到业务层。
- 事件中立化：SDK 的 Notification 在此翻译成 RuntimeEvent，
  业务层与前端永远看不到 Codex 原始事件格式（spec §24.9）。
- final_response 提取逻辑与 SDK `_run._final_assistant_response_from_items`
  保持一致（最后一个 phase=final_answer 的 AgentMessage）。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

from app.config import get_settings
from app.decision_harness.runtime import AgentRuntimeError, RuntimeEvent

try:  # openai-codex 可选依赖：pip install -e ".[codex]"
    from openai_codex import AsyncCodex, CodexConfig, Sandbox, TextInput
    from openai_codex.generated.v2_all import (
        AgentMessageThreadItem,
        ItemCompletedNotification,
        MessagePhase,
        ThreadItem,
        TurnCompletedNotification,
    )

    _CODEX_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖缺失分支
    AsyncCodex = None  # type: ignore[assignment]
    CodexConfig = None  # type: ignore[assignment]
    Sandbox = None  # type: ignore[assignment]
    TextInput = None  # type: ignore[assignment]
    AgentMessageThreadItem = None  # type: ignore[assignment]
    ItemCompletedNotification = None  # type: ignore[assignment]
    MessagePhase = None  # type: ignore[assignment]
    ThreadItem = None  # type: ignore[assignment]
    TurnCompletedNotification = None  # type: ignore[assignment]
    _CODEX_SDK_AVAILABLE = False

_VALID_SANDBOXES = ("read_only", "workspace_write", "full_access")


@dataclass
class _CodexSession:
    thread: Any  # AsyncThread；注入 fake client 时是任意对象
    thread_id: str
    instructions: str


def _payload_to_dict(payload: Any) -> dict[str, Any]:
    """SDK pydantic payload → JSON-safe dict（失败不炸，降级为字符串）。"""
    if payload is None:
        return {}
    if hasattr(payload, "model_dump"):
        try:
            return payload.model_dump(mode="json", exclude_none=True)
        except Exception:
            pass
    try:
        return json.loads(json.dumps(payload, default=str))
    except Exception:
        return {"raw": str(payload)}


def _final_assistant_response(items: list[Any]) -> str | None:
    """与 SDK 相同的最终回答提取规则：最后一个 final_answer，其次最后一个无 phase。"""
    last_unknown: str | None = None
    for item in reversed(items):
        thread_item = getattr(item, "root", item)
        if AgentMessageThreadItem is None or not isinstance(
            thread_item, AgentMessageThreadItem
        ):
            continue
        if thread_item.phase == MessagePhase.final_answer:
            return thread_item.text
        if thread_item.phase is None and last_unknown is None:
            last_unknown = thread_item.text
    return last_unknown


class CodexAgentRuntime:
    """AgentRuntime 协议的 Codex 实现。

    测试注入：codex_client 传 fake 时绕过 SDK 启动逻辑；
    未注入且 SDK 缺失时，构造即抛 codex_sdk_unavailable。
    """

    def __init__(
        self,
        *,
        codex_client: Any | None = None,
        model: str | None = None,
        codex_bin: str | None = None,
    ):
        self._injected_client = codex_client
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()
        self._sessions: dict[str, _CodexSession] = {}
        self._live_handles: dict[str, Any] = {}
        self._event_seq = 0
        settings = get_settings()
        self._model = model if model is not None else settings.codex_model or None
        # 指向深墨 Fork 自建二进制（CODEX_FORK_PIN 记录的构建产物）
        self._codex_bin = codex_bin or settings.codex_bin
        self._sandbox_name = settings.codex_sandbox
        if self._sandbox_name not in _VALID_SANDBOXES:
            raise AgentRuntimeError(
                "invalid_codex_sandbox",
                f"CODEX_SANDBOX={self._sandbox_name!r} 非法，可选: {_VALID_SANDBOXES}",
            )
        if codex_client is None and not _CODEX_SDK_AVAILABLE:
            raise AgentRuntimeError(
                "codex_sdk_unavailable",
                "openai-codex 未安装：pip install -e '.[codex]'（spec §24.4 可选依赖）",
                retryable=False,
            )

    # ---- 客户端生命周期 ----

    async def _ensure_client(self) -> Any:
        if self._injected_client is not None:
            return self._injected_client
        async with self._client_lock:
            if self._client is not None:
                return self._client
            if self._codex_bin:
                # 深墨 Fork 路径：CODEX_BIN 指向独立 codex-app-server 二进制，
                # 而非带 app-server 子命令的 codex CLI——SDK 默认 argv 会拼上
                # "app-server" 子命令导致启动失败，必须用 launch_args_override
                # 整替 argv（SDK client.start()：override 非空则完全替换）。
                kwargs: dict[str, Any] = {
                    "launch_args_override": (self._codex_bin, "--listen", "stdio://"),
                }
                if get_settings().codex_use_glm_gateway:
                    # 模型走智谱：CODEX_HOME 写 config.toml 指向 GLM 网关
                    from app.glm_gateway.provider import build_glm_codex_env

                    kwargs = build_glm_codex_env(codex_bin=self._codex_bin)
                client = AsyncCodex(CodexConfig(**kwargs))
            else:
                client = AsyncCodex()
            api_key = get_settings().codex_api_key
            if api_key:
                await client.login_api_key(api_key)
            self._client = client
            return client

    async def aclose(self) -> None:
        """关闭共享的 Codex 客户端（进程退出时调用）。会话线程保留、可 resume。"""
        client = self._client
        self._client = None
        if client is not None and hasattr(client, "close"):
            result = client.close()
            if asyncio.iscoroutine(result):
                await result

    def _sandbox(self) -> Any:
        # Sandbox.read_only 等是类级实例（部分版本是工厂函数），两种形态都兼容
        value = getattr(Sandbox, self._sandbox_name)
        return value() if callable(value) else value

    def _session(self, session_id: str) -> _CodexSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise AgentRuntimeError(
                "session_not_found", f"会话 {session_id} 未启动", retryable=True
            )
        return session

    # ---- AgentRuntime 协议 ----

    async def start_session(self, session_id: str, *, instructions: str) -> None:
        if session_id in self._sessions:
            raise AgentRuntimeError("session_exists", f"会话 {session_id} 已存在")
        client = await self._ensure_client()
        thread = await client.thread_start(
            model=self._model,
            sandbox=self._sandbox(),
            base_instructions=instructions,
        )
        self._sessions[session_id] = _CodexSession(
            thread=thread, thread_id=thread.id, instructions=instructions
        )

    async def resume_session(
        self, session_id: str, *, runtime_thread_id: str | None = None
    ) -> None:
        if session_id in self._sessions:
            return  # 已在内存中，幂等
        if not runtime_thread_id:
            raise AgentRuntimeError(
                "codex_thread_id_missing",
                f"恢复会话 {session_id} 需要 codex thread_id（repository 提供 root_thread_id）",
                retryable=False,
            )
        client = await self._ensure_client()
        thread = await client.thread_resume(
            runtime_thread_id, sandbox=self._sandbox()
        )
        self._sessions[session_id] = _CodexSession(
            thread=thread, thread_id=runtime_thread_id, instructions=""
        )

    async def start_turn(
        self, session_id: str, prompt: str, *, tools: list[str] | None = None
    ) -> AsyncIterator[RuntimeEvent]:
        session = self._session(session_id)
        handle = await session.thread.turn(TextInput(text=prompt))
        self._live_handles[session_id] = handle
        items: list[Any] = []
        try:
            async for notification in handle.stream():
                for event in self._translate(
                    notification, session.thread_id, items
                ):
                    yield event
        finally:
            self._live_handles.pop(session_id, None)

    def _translate(
        self, notification: Any, thread_id: str, items: list[Any]
    ) -> list[RuntimeEvent]:
        """一条 SDK Notification → 0..2 条中立 RuntimeEvent。"""
        method = getattr(notification, "method", "") or ""
        payload = getattr(notification, "payload", None)
        out: list[RuntimeEvent] = []

        if (
            ItemCompletedNotification is not None
            and isinstance(payload, ItemCompletedNotification)
        ):
            items.append(payload.item)
            out.append(self._event("item_completed", {
                "method": method,
                "item": _summarize_item(payload.item),
            }, thread_id))
            return out

        if (
            TurnCompletedNotification is not None
            and isinstance(payload, TurnCompletedNotification)
        ):
            turn = payload.turn
            if getattr(turn, "status", None) is not None and hasattr(turn.status, "value"):
                status = turn.status.value
            else:
                status = str(getattr(turn, "status", "unknown"))
            if status == "failed":
                error = getattr(turn, "error", None)
                message = getattr(error, "message", None) or "codex turn failed"
                out.append(self._event(
                    "turn_failed", {"message": message, "turn_id": turn.id}, thread_id
                ))
                return out
            final_text = _final_assistant_response(items)
            if final_text is not None:
                out.append(self._event(
                    "turn_final", {"final_response": final_text}, thread_id
                ))
            out.append(self._event(
                "turn_completed",
                {
                    "turn_id": turn.id,
                    "status": status,
                    "duration_ms": getattr(turn, "duration_ms", None),
                },
                thread_id,
            ))
            return out

        out.append(self._event(
            "runtime_notification",
            {"method": method, "payload": _payload_to_dict(payload)},
            thread_id,
        ))
        return out

    def _event(
        self, type_: str, payload: dict[str, Any], thread_id: str
    ) -> RuntimeEvent:
        self._event_seq += 1
        return RuntimeEvent(
            seq=self._event_seq, type=type_, payload=payload, thread_id=thread_id
        )

    async def interrupt_turn(self, session_id: str) -> None:
        handle = self._live_handles.get(session_id)
        if handle is None:
            return  # 无在途轮次：幂等（spec §7.4）
        await handle.interrupt()

    async def compact_session(self, session_id: str) -> None:
        session = self._session(session_id)
        await session.thread.compact()

    async def close_session(self, session_id: str) -> None:
        # 线程保留在 Codex runtime 侧，可随时 thread_resume（spec §7.7 重启恢复）
        self._sessions.pop(session_id, None)
        self._live_handles.pop(session_id, None)

    # ---- 持久化辅助 ----

    def runtime_thread_id(self, session_id: str) -> str | None:
        session = self._sessions.get(session_id)
        return session.thread_id if session else None


def _summarize_item(item: Any) -> dict[str, Any]:
    """item_completed 事件只带摘要，不带原始工具参数/大段文本（spec §15.2）。"""
    thread_item = getattr(item, "root", item)
    cls = type(thread_item).__name__
    summary: dict[str, Any] = {"item_type": cls}
    phase = getattr(thread_item, "phase", None)
    if phase is not None:
        summary["phase"] = getattr(phase, "value", str(phase))
    text = getattr(thread_item, "text", None)
    if isinstance(text, str):
        summary["text"] = text[:500]
    return summary
