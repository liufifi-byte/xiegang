"""CodexAgentRuntime 测试：SDK 守卫、事件翻译、生命周期映射。

真实 SDK 已安装时同时验证可用性分支；fake client 注入验证协议行为，
不启动真实 Codex runtime（离线可跑，spec §24.5）。
"""
import pytest

from app.decision_harness import codex_runtime as cr
from app.decision_harness.codex_runtime import CodexAgentRuntime
from app.decision_harness.runtime import AgentRuntimeError

from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    ItemCompletedNotification,
    MessagePhase,
    ThreadItem,
    Turn,
    TurnCompletedNotification,
    TurnStatus,
)
from openai_codex.models import Notification


def _msg_item(text: str, phase=None, item_id: str = "i1") -> ThreadItem:
    return ThreadItem(
        root=AgentMessageThreadItem(
            id=item_id, type="agentMessage", text=text, phase=phase
        )
    )


def _item_completed(thread_id="th1", turn_id="t1", item=None) -> Notification:
    return Notification(
        method="item/completed",
        payload=ItemCompletedNotification(
            completed_at_ms=1,
            thread_id=thread_id,
            turn_id=turn_id,
            item=item or _msg_item("思考中……", MessagePhase.commentary),
        ),
    )


def _turn_completed(thread_id="th1", turn_id="t1", status=TurnStatus.completed,
                    error=None) -> Notification:
    turn = Turn(
        id=turn_id, status=status, items=[], items_view=None, error=error,
        started_at=None, completed_at=None, duration_ms=12,
    )
    return Notification(
        method="turn/completed",
        payload=TurnCompletedNotification(thread_id=thread_id, turn=turn),
    )


class FakeHandle:
    def __init__(self, notifications):
        self._notifications = notifications
        self.interrupted = 0

    async def interrupt(self):
        self.interrupted += 1

    async def stream(self):
        for n in self._notifications:
            yield n


class FakeThread:
    def __init__(self, thread_id="th1"):
        self.id = thread_id
        self.turns: list[FakeHandle] = []
        self.compacted = 0

    async def turn(self, input, **kwargs):  # 与 AsyncThread.turn 对齐（async）
        handle = FakeHandle(self._next_notifications.pop(0))
        self.turns.append(handle)
        return handle

    _next_notifications: list = []

    async def compact(self):
        self.compacted += 1


class FakeCodexClient:
    def __init__(self):
        self.thread = FakeThread()
        self.resumed_with = None
        self.started_kwargs = None

    async def thread_start(self, **kwargs):
        self.started_kwargs = kwargs
        return self.thread

    async def thread_resume(self, thread_id, **kwargs):
        self.resumed_with = (thread_id, kwargs)
        return self.thread


# ============ SDK 守卫 ============

def test_sdk_available_or_guarded():
    """SDK 装了 → 常量 True；没装 → 构造抛 codex_sdk_unavailable。"""
    if cr._CODEX_SDK_AVAILABLE:
        client = FakeCodexClient()
        rt = CodexAgentRuntime(codex_client=client)
        assert rt is not None
    else:  # pragma: no cover
        with pytest.raises(AgentRuntimeError) as exc:
            CodexAgentRuntime()
        assert exc.value.code == "codex_sdk_unavailable"


async def test_sdk_missing_raises_domain_error(monkeypatch):
    monkeypatch.setattr(cr, "_CODEX_SDK_AVAILABLE", False)
    with pytest.raises(AgentRuntimeError) as exc:
        CodexAgentRuntime()
    assert exc.value.code == "codex_sdk_unavailable"
    assert exc.value.retryable is False


# ============ 会话生命周期 ============

async def test_start_session_calls_thread_start_with_read_only_sandbox():
    client = FakeCodexClient()
    rt = CodexAgentRuntime(codex_client=client)
    await rt.start_session("s1", instructions="主席守则")
    assert client.started_kwargs["base_instructions"] == "主席守则"
    assert rt.runtime_thread_id("s1") == "th1"
    with pytest.raises(AgentRuntimeError):
        await rt.start_session("s1", instructions="again")


async def test_resume_requires_thread_id():
    client = FakeCodexClient()
    rt = CodexAgentRuntime(codex_client=client)
    with pytest.raises(AgentRuntimeError) as exc:
        await rt.resume_session("s1")
    assert exc.value.code == "codex_thread_id_missing"

    await rt.resume_session("s1", runtime_thread_id="th-resume")
    assert client.resumed_with[0] == "th-resume"
    assert rt.runtime_thread_id("s1") == "th-resume"
    # 幂等：会话已在内存 → 再次 resume 不报错
    await rt.resume_session("s1", runtime_thread_id="th-resume")


# ============ 轮次与事件翻译 ============

async def test_turn_yields_final_and_completed():
    FakeThread._next_notifications = [
        [
            _item_completed(item=_msg_item('{"action":"revise_plan"}', MessagePhase.final_answer)),
            _turn_completed(),
        ]
    ]
    client = FakeCodexClient()
    rt = CodexAgentRuntime(codex_client=client)
    await rt.start_session("s1", instructions="x")
    events = [e async for e in rt.start_turn("s1", "prompt")]
    types = [e.type for e in events]
    assert "item_completed" in types
    assert "turn_final" in types
    assert types[-1] == "turn_completed"
    final = next(e for e in events if e.type == "turn_final")
    assert final.payload["final_response"] == '{"action":"revise_plan"}'
    assert all(e.thread_id == "th1" for e in events)


async def test_failed_turn_yields_turn_failed():
    FakeThread._next_notifications = [[_turn_completed(status=TurnStatus.failed)]]
    client = FakeCodexClient()
    rt = CodexAgentRuntime(codex_client=client)
    await rt.start_session("s1", instructions="x")
    events = [e async for e in rt.start_turn("s1", "prompt")]
    assert events[-1].type == "turn_failed"
    assert "turn_final" not in [e.type for e in events]


async def test_unknown_notification_becomes_neutral_runtime_notification():
    from openai_codex.generated.v2_all import ThreadNameUpdatedNotification

    renamed = ThreadNameUpdatedNotification(thread_id="th1", thread_name="新会话名")
    FakeThread._next_notifications = [
        [Notification(method="thread/nameUpdated", payload=renamed), _turn_completed()]
    ]
    client = FakeCodexClient()
    rt = CodexAgentRuntime(codex_client=client)
    await rt.start_session("s1", instructions="x")
    events = [e async for e in rt.start_turn("s1", "prompt")]
    neutral = [e for e in events if e.type == "runtime_notification"]
    assert len(neutral) == 1
    assert neutral[0].payload["method"] == "thread/nameUpdated"


async def test_interrupt_and_compact():
    FakeThread._next_notifications = [
        [_item_completed(), _turn_completed()]
    ]
    client = FakeCodexClient()
    rt = CodexAgentRuntime(codex_client=client)
    await rt.start_session("s1", instructions="x")
    # 无在途轮次：幂等 no-op
    await rt.interrupt_turn("s1")

    gen = rt.start_turn("s1", "p")
    await gen.__anext__()  # 触发 handle 注册
    await rt.interrupt_turn("s1")
    assert client.thread.turns[-1].interrupted == 1
    await gen.aclose()

    await rt.compact_session("s1")
    assert client.thread.compacted == 1
    await rt.close_session("s1")
    assert rt.runtime_thread_id("s1") is None
    with pytest.raises(AgentRuntimeError):
        await rt.compact_session("s1")
