"""P0-S1.1 限流测试（Codex fix: 并发检查走 DB 而非 in-memory set）。"""
import asyncio
import time
from unittest.mock import patch, MagicMock

import pytest

from app.middleware.rate_limit import (
    _ACTIVE_DEVICE_CACHE,
    _check_and_record,
    _clear_all_for_tests,
    _get_device_id,
    _IP_REQUESTS,
    _DEVICE_DAILY,
    mark_device_finished,
)


@pytest.fixture(autouse=True)
def clean():
    _clear_all_for_tests()
    _ACTIVE_DEVICE_CACHE.clear()
    yield
    _clear_all_for_tests()
    _ACTIVE_DEVICE_CACHE.clear()


def _mock_no_active_parliament():
    """Mock 返回无活跃议会（允许创建）"""
    mock_result = MagicMock()
    mock_result.data = []
    return mock_result


def _mock_has_active_parliament():
    """Mock 返回有活跃议会（应被拦）"""
    mock_result = MagicMock()
    mock_result.data = [{"id": "xxx"}]
    return mock_result


def test_first_request_allowed():
    with patch("app.middleware.rate_limit._has_active_parliament", return_value=asyncio.coroutine(lambda: False)()) if False else patch("app.db.get_supabase") as mock_supa:
        mock_table = MagicMock()
        mock_table.select.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value = _mock_no_active_parliament()
        mock_supa.return_value.table.return_value = mock_table
        allowed, _ = asyncio.run(_check_and_record(
            ip="1.1.1.1", device_id="d1",
            ip_per_minute=2, device_daily=10,
        ))
        assert allowed is True


def test_ip_per_minute_limit():
    """同 IP 第 3 次应该被拒。"""
    with patch("app.db.get_supabase") as mock_supa:
        mock_table = MagicMock()
        mock_table.select.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value = _mock_no_active_parliament()
        mock_supa.return_value.table.return_value = mock_table
        asyncio.run(_check_and_record("1.1.1.1", "d1", 2, 10))
        asyncio.run(_check_and_record("1.1.1.1", "d2", 2, 10))
        allowed, reason = asyncio.run(_check_and_record("1.1.1.1", "d3", 2, 10))
        assert allowed is False
        assert "每分钟" in reason


def test_device_daily_limit():
    """同 device 第 11 次应该被拒。"""
    with patch("app.db.get_supabase") as mock_supa:
        mock_table = MagicMock()
        mock_table.select.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value = _mock_no_active_parliament()
        mock_supa.return_value.table.return_value = mock_table
        for i in range(10):
            asyncio.run(_check_and_record(f"1.1.1.{i}", "same-device", 100, 10))
        allowed, reason = asyncio.run(_check_and_record("1.1.1.99", "same-device", 100, 10))
        assert allowed is False
        assert "上限" in reason


def test_concurrent_device_blocked():
    """同 device 第二次应该被拒（DB 查到活跃议会）。"""
    with patch("app.db.get_supabase") as mock_supa:
        mock_table = MagicMock()
        # 先放行第一次
        mock_table.select.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value = _mock_has_active_parliament()
        mock_supa.return_value.table.return_value = mock_table
        # 第二次查 DB 返回有活跃议会 → 拒
        allowed, reason = asyncio.run(_check_and_record("2.2.2.2", "d1", 10, 10))
        assert allowed is False
        assert "活跃会议" in reason or "正在" in reason


def test_no_active_allows_creation():
    """DB 无活跃议会（done/cancelled）→ 允许创建。"""
    with patch("app.db.get_supabase") as mock_supa:
        mock_table = MagicMock()
        mock_table.select.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value = _mock_no_active_parliament()
        mock_supa.return_value.table.return_value = mock_table
        allowed, reason = asyncio.run(_check_and_record("1.1.1.1", "d1", 10, 10))
        assert allowed is True


class _MockRequest:
    def __init__(self, headers, client_host="1.1.1.1"):
        self.headers = headers
        self.client = type("C", (), {"host": client_host})()


def test_get_device_id_from_header():
    req = _MockRequest({"X-Device-Id": "test-uuid-1234"})
    assert _get_device_id(req) == "test-uuid-1234"


def test_get_device_id_fallback_to_ip():
    req = _MockRequest({})
    result = _get_device_id(req)
    assert result.startswith("ip:")


def test_get_device_id_via_x_forwarded_for():
    from app.middleware.rate_limit import _get_client_ip
    req = _MockRequest({"X-Forwarded-For": "5.5.5.5, 6.6.6.6"})
    assert _get_client_ip(req) == "5.5.5.5"
