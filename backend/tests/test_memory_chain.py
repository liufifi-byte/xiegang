"""跨议会记忆链路修复（V2-S3b）回归。

修复内容：
1. 建议会缺省 user_id 回落到设备 UUID（合法 UUID 才回落）→ memory_search
   得以下发、user_memories 读写闭环。
2. match_user_memories RPC 按 user_id 隔离（DB 侧已升级 + 删旧重载 +
   删两个指向空 users 表的死外键），代码侧 filter_user_id 传参。
"""
import uuid as uuid_lib

DEVICE = "e278f045-400e-4d71-9027-c209d16fcbe7"


def test_create_defaults_user_id_to_device_uuid(client, monkeypatch):
    """浏览器设备 UUID → user_id 自动回落，记忆链路激活。"""
    from app.api import parliament as parl_api

    inserted = {}

    class MockSupabase:
        def table(self, name):
            class Table:
                def insert(self, data):
                    inserted.update(data)

                    class Exec:
                        def execute(self_inner):
                            class Result:
                                data = [{"id": "pid"}]
                            return Result()
                    return Exec()
            return Table()

    monkeypatch.setattr(parl_api, "get_supabase", lambda: MockSupabase())
    resp = client.post(
        "/api/parliament",
        json={"topic": "买房"},
        headers={"X-Device-Id": DEVICE},
    )
    assert resp.status_code == 200
    assert inserted["user_id"] == DEVICE


def test_create_leaves_user_id_none_for_non_uuid_device(client, monkeypatch):
    """ip:xxx 类设备标识不是 uuid（列类型 uuid），宁可 None 也不炸 insert。"""
    from app.api import parliament as parl_api

    inserted = {}

    class MockSupabase:
        def table(self, name):
            class Table:
                def insert(self, data):
                    inserted.update(data)

                    class Exec:
                        def execute(self_inner):
                            class Result:
                                data = [{"id": "pid"}]
                            return Result()
                    return Exec()
            return Table()

    monkeypatch.setattr(parl_api, "get_supabase", lambda: MockSupabase())
    resp = client.post(
        "/api/parliament",
        json={"topic": "买房"},
        headers={"X-Device-Id": "ip:127.0.0.1"},
    )
    assert resp.status_code == 200
    assert inserted["user_id"] is None


def test_explicit_user_id_wins(client, monkeypatch):
    from app.api import parliament as parl_api

    inserted = {}
    explicit = str(uuid_lib.uuid4())

    class MockSupabase:
        def table(self, name):
            class Table:
                def insert(self, data):
                    inserted.update(data)

                    class Exec:
                        def execute(self_inner):
                            class Result:
                                data = [{"id": "pid"}]
                            return Result()
                    return Exec()
            return Table()

    monkeypatch.setattr(parl_api, "get_supabase", lambda: MockSupabase())
    resp = client.post(
        "/api/parliament",
        json={"topic": "买房", "user_id": explicit},
        headers={"X-Device-Id": DEVICE},
    )
    assert resp.status_code == 200
    assert inserted["user_id"] == explicit


def test_memory_query_passes_user_filter(monkeypatch):
    """memory_query 必须把 user_id 传给 RPC（跨用户泄漏修复）。"""
    import app.tools.memory_query as mq

    captured = {}

    class FakeRpc:
        def __init__(self, params):
            captured.update(params)

        def execute(self):
            class R:
                data = []
            return R()

    class FakeSupabase:
        def rpc(self, name, params):
            assert name == "match_user_memories"
            return FakeRpc(params)

    monkeypatch.setattr(mq, "embed_query_sync", lambda text: [0.1, 0.2])
    import app.db as db
    monkeypatch.setattr(db, "get_supabase", lambda: FakeSupabase())
    monkeypatch.setattr(mq, "get_supabase", lambda: FakeSupabase(), raising=False)

    mq.memory_query(user_id=DEVICE, topic_category="offer", query_text="要不要接 offer")
    assert captured.get("filter_user_id") == DEVICE

    captured.clear()
    mq.memory_query(user_id=None, topic_category=None, query_text="要不要接 offer")
    assert captured.get("filter_user_id") is None
