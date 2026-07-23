"""P0-S1.0 修复的回归测试。"""
import asyncio

import pytest

from app.runtime_context import _clear_all_for_tests


@pytest.fixture(autouse=True)
def clean_ctx():
    _clear_all_for_tests()
    yield
    _clear_all_for_tests()


# ============ Bug 2: stocks / buy_bag 进白名单 ============

def test_valid_categories_includes_stocks_and_buy_bag():
    """P0-S1.0: 前端有的 stocks / buy_bag 必须在后端白名单。"""
    from app.graph.nodes.parse_topic import VALID_CATEGORIES
    assert "stocks" in VALID_CATEGORIES
    assert "buy_bag" in VALID_CATEGORIES
    assert "real_estate" in VALID_CATEGORIES
    assert "general" in VALID_CATEGORIES


# ============ Bug 1+4: archive 字段名 + finished_at ============

def test_archive_writes_correct_field_names(monkeypatch):
    """P0-S1.0: archive 应写 topic_category 不是 category，且带 finished_at / duration。"""
    from app.graph.nodes.archive import run_archive

    captured_updates = []

    class MockExec:
        def execute(self_inner):
            class MockInsertResult:
                data = [{"id": "memory-1"}]
            return MockInsertResult()

    class MockTable:
        def update(self, data):
            captured_updates.append(("update", data))
            return self
        def eq(self, *a, **kw):
            return self
        def insert(self, *a, **kw):
            return MockExec()

    class MockSupabase:
        def table(self, name):
            return MockTable()

    monkeypatch.setattr("app.graph.nodes.archive.get_supabase", lambda: MockSupabase())
    monkeypatch.setattr("app.embedding.embed_query_sync", lambda t: [0.0] * 2000)
    from app.graph.nodes import archive as ar_module
    monkeypatch.setattr(ar_module, "_update_embedding_via_pgsql", lambda *a, **kw: None)

    state = {
        "parliament_id": "test-1",
        "category": "stocks",   # 测试关键
        "topic": "test topic",
        "members": {},
        "summary": {},
        "transcript": [],
        "tool_calls": [],
        "election_result": None,
        "tension_score": 0.0,
        "started_at": 1700000000.0,
        "user_id": "u1",
    }
    run_archive(state)

    # 找到 update 操作
    update_calls = [u for u in captured_updates if u[0] == "update"]
    assert len(update_calls) >= 1
    update_data = update_calls[0][1]

    # 修复 1：用 topic_category 不是 category
    assert "topic_category" in update_data
    assert "category" not in update_data
    assert update_data["topic_category"] == "stocks"

    # 修复 4：带 finished_at / total_duration_seconds / status
    assert update_data["status"] == "done"
    assert "finished_at" in update_data
    assert "total_duration_seconds" in update_data
    assert update_data["total_duration_seconds"] > 0


# ============ Bug 5 已被 P0-S2 替代：worker 接管任务生命周期 ============
# _mark_parliament_failed 在 stream.py 已被删除
# 现在 worker.py 的 run_one_parliament 通过 archive 节点自然写 status='done'
# 异常路径由 worker_loop 捕获 + 通过 append_event('error') 通知前端


def test_worker_run_one_parliament_handles_exception(monkeypatch):
    """worker.py 异常不会让 worker_loop 整体崩。"""
    from app import worker

    async def fake_get_graph(*a, **kw):
        raise RuntimeError("GLM API 挂了")

    monkeypatch.setattr(worker, "get_graph_with_checkpointer", fake_get_graph)
    # mark_running 用 in-memory 路径
    monkeypatch.setenv("SKIP_DB_CONTEXT", "true")

    def fake_append_event(*a, **kw):
        return 1

    monkeypatch.setattr(worker, "append_event", fake_append_event)

    async def fake_get_or_create(parliament_id):
        class C:
            pass

        return C()

    async def fake_noop(*a, **kw):
        return None

    monkeypatch.setattr(worker, "get_or_create_context", fake_get_or_create)
    monkeypatch.setattr(worker, "mark_running", fake_noop)
    monkeypatch.setattr(worker, "mark_finished", fake_noop)
    monkeypatch.setattr(worker, "_mark_terminal_status", fake_noop)

    task = {
        "id": "p-test",
        "topic": "测试",
        "topic_category": "general",
        "user_background": None,
        "user_id": None,
        "device_id": "d1",
    }
    # 不应该抛异常
    import asyncio
    asyncio.run(worker.run_one_parliament(task))


def test_worker_timeout_emits_error_without_false_done(monkeypatch):
    """超时是一种终止状态，不能紧接着再发送 parliament_done。"""
    import asyncio
    from types import SimpleNamespace

    from app import worker

    class FakeSnapshot:
        next = ()

    class FakeGraph:
        async def aget_state(self, config):
            return FakeSnapshot()

        async def astream(self, run_input, config):
            yield {"parse_topic": {"category": "general"}}

    async def fake_get_graph():
        return FakeGraph()

    async def fake_context(*args, **kwargs):
        return SimpleNamespace()

    async def fake_noop(*args, **kwargs):
        return None

    events = []
    terminal_statuses = []

    def fake_append_event(parliament_id, event_type, payload):
        events.append(event_type)
        return len(events)

    async def fake_terminal_status(parliament_id, status, error_message=None):
        terminal_statuses.append((status, error_message))

    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(parliament_total_timeout=-1),
    )
    monkeypatch.setattr(worker, "get_graph_with_checkpointer", fake_get_graph)
    monkeypatch.setattr(worker, "get_or_create_context", fake_context)
    monkeypatch.setattr(worker, "mark_running", fake_noop)
    monkeypatch.setattr(worker, "mark_finished", fake_noop)
    monkeypatch.setattr(worker, "_mark_terminal_status", fake_terminal_status)
    monkeypatch.setattr(worker, "append_event", fake_append_event)

    asyncio.run(
        worker.run_one_parliament(
            {
                "id": "p-timeout",
                "topic": "测试超时",
                "topic_category": "general",
                "user_background": None,
                "user_id": None,
                "device_id": "d1",
            }
        )
    )
    assert events == ["parliament_started", "error"]
    assert terminal_statuses == [("failed", "parliament_timeout_-1s")]


def test_reclaim_stale_tasks_returns_int(monkeypatch):
    """启动回收返回整数。"""
    from app import worker

    def fake_do():
        return 0

    async def fake_to_thread(fn, *a, **kw):
        return fn()

    # mock psycopg 让内部 _do() 不连真 DB
    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, *a, **kw): pass
        def fetchall(self): return []
    class FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def cursor(self): return FakeCursor()
        def commit(self): pass
    import app.worker as w
    monkeypatch.setattr(w.psycopg, "connect", lambda *a, **kw: FakeConn())

    result = asyncio.run(worker._reclaim_stale_tasks())
    assert isinstance(result, int)
    assert result == 0  # fetchall 返回空
