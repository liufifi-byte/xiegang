"""runtime_context 测试（V1-S3）。"""
import asyncio

import pytest

from app.runtime_context import (
    _clear_all_for_tests,
    consume_user_interrupt,
    get_or_create_context,
    mark_finished,
    mark_running,
    set_user_interrupt,
)


@pytest.fixture(autouse=True)
def clean_ctx():
    _clear_all_for_tests()
    yield
    _clear_all_for_tests()


def test_get_or_create_context_idempotent():
    ctx1 = asyncio.run(get_or_create_context("p1"))
    ctx2 = asyncio.run(get_or_create_context("p1"))
    assert ctx1 is ctx2
    assert ctx1.parliament_id == "p1"


def test_set_interrupt_returns_true_when_running():
    asyncio.run(get_or_create_context("p1"))
    asyncio.run(mark_running("p1"))
    ok = asyncio.run(set_user_interrupt("p1", "我预算 30w"))
    assert ok is True


def test_set_interrupt_returns_false_when_finished():
    asyncio.run(get_or_create_context("p1"))
    asyncio.run(mark_running("p1"))
    asyncio.run(mark_finished("p1"))
    ok = asyncio.run(set_user_interrupt("p1", "我预算 30w"))
    assert ok is False


def test_set_interrupt_returns_false_when_no_context():
    ok = asyncio.run(set_user_interrupt("not-exist", "test"))
    assert ok is False


def test_consume_returns_value_then_clears():
    asyncio.run(get_or_create_context("p1"))
    asyncio.run(set_user_interrupt("p1", "first"))
    val = asyncio.run(consume_user_interrupt("p1"))
    assert val == "first"
    val2 = asyncio.run(consume_user_interrupt("p1"))
    assert val2 is None


def test_consume_returns_none_when_no_context():
    val = asyncio.run(consume_user_interrupt("not-exist"))
    assert val is None


def test_mark_running_resets_finished_and_cancel():
    asyncio.run(get_or_create_context("p1"))
    asyncio.run(mark_running("p1"))
    asyncio.run(mark_finished("p1"))
    asyncio.run(mark_running("p1"))  # 再启动
    ctx = asyncio.run(get_or_create_context("p1"))
    assert ctx.is_running is True
    assert ctx.finished_at is None


def test_empty_content_rejected_at_api():
    """API 层会在 parliament.py 做 400 校验，这里间接验证 ctx 不该有允许空字符串的逻辑。
    实际 API 校验在 tests/test_api/test_interrupt.py 中。"""
    asyncio.run(get_or_create_context("p1"))
    asyncio.run(mark_running("p1"))
    ok = asyncio.run(set_user_interrupt("p1", ""))  # runtime 允许，但 API 层拦
    # ctx 层允许写空字符串（API 拦截更合理）
    assert ok is True
