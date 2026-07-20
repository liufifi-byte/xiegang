"""archive 节点测试（不打真实数据库）。"""
from app.graph.nodes.archive import run_archive


def test_archive_skips_without_parliament_id():
    """没 parliament_id 直接返回，不调 DB（测试场景）。"""
    result = run_archive({"parliament_id": None})
    assert "finished_at" in result


def test_archive_handles_db_write_error(monkeypatch):
    """DB 写入失败时应该返回 error_message，不抛异常。"""
    from app.graph.nodes import archive as ar_module

    class FailingExecute:
        def execute(self):
            raise RuntimeError("DB connection failed")

    class MockTable:
        def update(self, data):
            return self
        def eq(self, col, val):
            return self
        def insert(self, row):
            return self
        def execute(self):
            return FailingExecute().execute()

    class MockSupabase:
        def table(self, name):
            return MockTable()

    monkeypatch.setattr(ar_module, "get_supabase", lambda: MockSupabase())

    state = {
        "parliament_id": "test-id",
        "members": {},
        "summary": {},
        "category": "general",
        "topic": "test",
    }
    result = run_archive(state)
    assert "error_message" in result
    assert "archive failed" in result["error_message"]


def test_archive_computes_duration(monkeypatch):
    """有 started_at 时算 duration。"""
    from app.graph.nodes import archive as ar_module
    import time as time_mod

    class SuccessExecute:
        def execute(self_inner):
            return None

    class MockInsertExec:
        def execute(self_inner):
            class MockResult:
                data = [{"id": "memory-test-id"}]
            return MockResult()

    class MockTable:
        def update(self, data):
            return self
        def eq(self, col, val):
            return self
        def insert(self, row):
            return MockInsertExec()
        def execute(self):
            # update / single 路径走这边
            return SuccessExecute().execute()

    class MockSupabase:
        def table(self, name):
            return MockTable()

    monkeypatch.setattr(ar_module, "get_supabase", lambda: MockSupabase())
    # mock 掉 embedding + pg vector 写入（避免真打 GLM API 和 DB）
    monkeypatch.setattr(
        "app.embedding.embed_query_sync",
        lambda text: [0.0] * 2000,
    )
    monkeypatch.setattr(ar_module, "_update_embedding_via_pgsql", lambda *args, **kwargs: None)

    state = {
        "parliament_id": "test-id-2",
        "members": {},
        "summary": {},
        "category": "general",
        "topic": "test",
        "started_at": time_mod.time() - 100,  # 100 秒前
    }
    result = run_archive(state)
    assert "total_duration_seconds" in result
    assert 90 < result["total_duration_seconds"] < 110
