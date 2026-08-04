"""测试 L3 orphan 自愈的 SQL 和阈值配置。"""
import importlib

import pytest

from app import worker
from app.config import Settings


def test_reclaim_threshold_uses_settings(monkeypatch):
    """_reclaim_stale_tasks 应当读 settings.parliament_stale_seconds，不再硬编码 900s。"""

    class FakeSettings:
        parliament_stale_seconds = 60  # 不同于默认 120

    monkeypatch.setattr(worker, "get_settings", lambda: FakeSettings())

    captured = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return []

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def cursor(self):
            return FakeCursor()

        def commit(self):
            captured["committed"] = True

    class FakePsycopg:
        @staticmethod
        def connect(url, **kwargs):
            captured["connected"] = True
            return FakeConn()

    monkeypatch.setattr(worker, "psycopg", FakePsycopg())
    monkeypatch.setattr(worker, "_clean_db_url", lambda: "fake://")

    import asyncio
    result = asyncio.run(worker._reclaim_stale_tasks())

    assert result == 0
    # SQL 应当用参数化阈值（不再硬编码 '15 minutes'）
    assert "15 minutes" not in captured["sql"]
    assert "seconds" in captured["sql"]
    # 参数应当是配置的阈值
    assert "60" in (captured["params"] or [])


def test_settings_has_orphan_threshold_setting():
    """Settings 类应当有 parliament_stale_seconds 字段，默认 120。"""
    # 直接检查 Settings 类的字段默认值（不实例化，避免依赖 .env）
    fields = Settings.model_fields
    assert "parliament_stale_seconds" in fields
    assert fields["parliament_stale_seconds"].default == 120


def test_settings_has_per_parliament_concurrency():
    """Settings 类应当有 per_parliament_llm_concurrency，默认 2。"""
    fields = Settings.model_fields
    assert "per_parliament_llm_concurrency" in fields
    assert fields["per_parliament_llm_concurrency"].default == 2


def test_settings_has_worker_allow_duplicate():
    """Settings 类应当有 worker_allow_duplicate，默认 False。"""
    fields = Settings.model_fields
    assert "worker_allow_duplicate" in fields
    assert fields["worker_allow_duplicate"].default is False
