import asyncio

import pytest

from app import worker


@pytest.mark.asyncio
async def test_cancel_watcher_cancels_worker_task(monkeypatch):
    checks = 0

    async def fake_is_cancelled(parliament_id):
        nonlocal checks
        checks += 1
        return checks >= 1

    monkeypatch.setattr(worker, "is_cancelled", fake_is_cancelled)

    async def long_running():
        task = asyncio.current_task()
        watcher = asyncio.create_task(
            worker._cancel_when_requested("p1", task, poll_seconds=0.001)
        )
        try:
            await asyncio.sleep(10)
        finally:
            watcher.cancel()

    with pytest.raises(asyncio.CancelledError):
        await long_running()
