"""P0-S1.1 限流测试。"""
import asyncio
import time

import pytest

from app.middleware.rate_limit import (
    _check_and_record,
    _clear_all_for_tests,
    _get_device_id,
    _RUNNING_DEVICES,
    _IP_REQUESTS,
    _DEVICE_DAILY,
    mark_device_finished,
)


@pytest.fixture(autouse=True)
def clean():
    _clear_all_for_tests()
    yield
    _clear_all_for_tests()


def test_first_request_allowed():
    allowed, _ = asyncio.run(_check_and_record(
        ip="1.1.1.1", device_id="d1",
        ip_per_minute=2, device_daily=10,
    ))
    assert allowed is True


def test_ip_per_minute_limit():
    """同 IP 第 3 次应该被拒。"""
    asyncio.run(_check_and_record("1.1.1.1", "d1", 2, 10))
    asyncio.run(_check_and_record("1.1.1.1", "d2", 2, 10))
    allowed, reason = asyncio.run(_check_and_record("1.1.1.1", "d3", 2, 10))
    assert allowed is False
    assert "每分钟" in reason


def test_device_daily_limit():
    """同 device 第 11 次（默认每天 10 场）应该被拒。"""
    for i in range(10):
        asyncio.run(_check_and_record(f"1.1.1.{i}", "same-device", 100, 10))
        # 每次后释放并发占位（不然会被并发上限挡）
        asyncio.run(mark_device_finished("same-device"))
    allowed, reason = asyncio.run(_check_and_record("1.1.1.99", "same-device", 100, 10))
    assert allowed is False
    assert "上限" in reason


def test_concurrent_device_blocked():
    """同 device 第二次应该被拒（并发=1）。"""
    asyncio.run(_check_and_record("1.1.1.1", "d1", 10, 10))
    allowed, reason = asyncio.run(_check_and_record("2.2.2.2", "d1", 10, 10))
    assert allowed is False
    assert "正在运行" in reason


def test_mark_finished_frees_slot():
    """结束后能再次开新议会。"""
    asyncio.run(_check_and_record("1.1.1.1", "d1", 10, 10))
    asyncio.run(mark_device_finished("d1"))
    allowed, _ = asyncio.run(_check_and_record("2.2.2.2", "d1", 10, 10))
    assert allowed is True


def test_different_devices_independent():
    """同 IP 下不同 device 互不影响（device 维度分开算）。"""
    asyncio.run(_check_and_record("1.1.1.1", "d1", 1, 10))
    asyncio.run(mark_device_finished("d1"))
    allowed, reason = asyncio.run(_check_and_record("1.1.1.1", "d2", 1, 10))
    # 同 IP 第 2 次但 d2，会被 IP 限制挡（ip_per_minute=1）
    assert allowed is False
    assert "IP" in reason or "每分钟" in reason


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
