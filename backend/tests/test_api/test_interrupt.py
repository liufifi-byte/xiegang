"""interrupt API 测试（V1-S3）。"""
import asyncio

import pytest

from app.runtime_context import _clear_all_for_tests


@pytest.fixture(autouse=True)
def clean_ctx():
    _clear_all_for_tests()
    yield
    _clear_all_for_tests()


def test_interrupt_rejects_empty_content(client):
    """空内容应返回 400。"""
    response = client.post("/api/parliament/p-test/interrupt", json={"content": "  "})
    assert response.status_code == 400


def test_interrupt_returns_404_when_parliament_not_running(client):
    """议会不存在或已结束 → 404。"""
    response = client.post(
        "/api/parliament/non-existent-id/interrupt",
        json={"content": "我预算 30w"},
    )
    assert response.status_code == 404


def test_interrupt_succeeds_when_parliament_running(client, monkeypatch):
    from app.api import parliament as parl_api
    from app.runtime_context import mark_running, get_or_create_context

    # 预置：一个运行中的 ctx
    asyncio.run(get_or_create_context("p-running"))
    asyncio.run(mark_running("p-running"))

    response = client.post(
        "/api/parliament/p-running/interrupt",
        json={"content": "我预算 30w"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["queued_at"] is True
