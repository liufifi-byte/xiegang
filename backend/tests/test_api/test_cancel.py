"""cancel API 测试（V1-S4）。"""
import asyncio

import pytest

from app.runtime_context import _clear_all_for_tests


@pytest.fixture(autouse=True)
def clean_ctx():
    _clear_all_for_tests()
    yield
    _clear_all_for_tests()


def test_cancel_returns_404_when_parliament_not_running(client):
    response = client.post("/api/parliament/non-existent-id/cancel")
    assert response.status_code == 404


def test_cancel_succeeds_when_parliament_running(client):
    from app.runtime_context import get_or_create_context, mark_running

    asyncio.run(get_or_create_context("p-running"))
    asyncio.run(mark_running("p-running"))

    response = client.post("/api/parliament/p-running/cancel")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "取消请求" in data["message"]
