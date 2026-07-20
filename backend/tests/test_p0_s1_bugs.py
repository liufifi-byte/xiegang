"""P0-S1.0 修复的回归测试。"""
import asyncio
from datetime import datetime

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
    result = run_archive(state)

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


# ============ Bug 5: _mark_parliament_failed 真写 DB ============

def test_mark_parliament_failed_writes_failed_status(monkeypatch):
    """P0-S1.0: 节点异常时 _mark_parliament_failed 真把 status 标 failed。"""
    from app.api.stream import _mark_parliament_failed

    captured = {}

    class MockExec:
        def execute(self_inner):
            return None

    class MockTable:
        def update(self, data):
            captured["data"] = data
            return self
        def eq(self, col, val):
            captured[col] = val
            return self

    class MockSupabase:
        def table(self, name):
            return MockTable()

    monkeypatch.setattr("app.api.stream.get_supabase", lambda: MockSupabase())

    asyncio.run(_mark_parliament_failed("p-broken", "test error"))

    assert captured.get("id") == "p-broken"
    assert captured["data"]["status"] == "failed"
    assert "finished_at" in captured["data"]
    assert "test error" in captured["data"]["error_message"]


def test_mark_parliament_failed_handles_db_failure(monkeypatch):
    """P0-S1.0: 即使 DB 本身炸了，_mark_parliament_failed 也不该再抛异常。"""
    from app.api.stream import _mark_parliament_failed

    def raise_err():
        raise RuntimeError("DB 宕机")

    class MockTable:
        def update(self, *a, **kw):
            raise_err()
        def eq(self, *a, **kw):
            return self

    class MockSupabase:
        def table(self, name):
            return MockTable()

    monkeypatch.setattr("app.api.stream.get_supabase", lambda: MockSupabase())

    # 不应该抛异常
    asyncio.run(_mark_parliament_failed("p-x", "test"))
