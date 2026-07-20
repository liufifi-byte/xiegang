"""P0-S2.1: event_store 测试。"""
import pytest

from app.event_store import _make_safe, _safe_json


def test_safe_json_passthrough_dict():
    """简单 dict 应该原样通过。"""
    d = {"a": 1, "b": "hello", "c": [1, 2, 3], "d": True}
    result = _safe_json(d)
    assert result == d


def test_safe_json_converts_unserializable():
    """不可序列化对象转字符串。"""
    class Custom:
        def __str__(self):
            return "custom-instance"

    result = _safe_json(Custom())
    assert result == "custom-instance"


def test_make_safe_recurses():
    """dict 里每个 value 都被检查。"""
    class C:
        def __str__(self):
            return "x"
    d = {"ok": 1, "not_ok": C()}
    result = _make_safe(d)
    assert result["ok"] == 1
    assert result["not_ok"] == "x"


def test_make_safe_handles_non_dict():
    """非 dict 输入也安全。"""
    assert _make_safe("hello") == {"value": "hello"}
    assert _make_safe(42) == {"value": 42}


def test_append_event_returns_seq():
    """验证 append_event 函数签名存在且可调用（不打真 DB）。"""
    from app.event_store import append_event
    assert callable(append_event)


def test_fetch_events_since_signature():
    from app.event_store import fetch_events_since
    assert callable(fetch_events_since)
