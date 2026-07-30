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


@pytest.mark.asyncio
async def test_worker_releases_waiting_clarification_without_false_done(monkeypatch):
    from types import SimpleNamespace

    class FakeSnapshot:
        next = ()

    class FakeGraph:
        async def aget_state(self, config):
            return FakeSnapshot()

        async def astream(self, run_input, config):
            yield {
                "persist_clarification": {
                    "clarification_waiting": True,
                    "clarification_questions": [{"id": "q1"}],
                }
            }

    async def fake_graph():
        return FakeGraph()

    async def fake_context(*args, **kwargs):
        return SimpleNamespace()

    async def fake_noop(*args, **kwargs):
        return None

    async def never_cancelled(*args, **kwargs):
        return False

    events = []

    def fake_append(parliament_id, event_type, payload):
        events.append(event_type)
        return len(events)

    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(parliament_total_timeout=300),
    )
    monkeypatch.setattr(worker, "get_graph_with_checkpointer", fake_graph)
    monkeypatch.setattr(worker, "get_or_create_context", fake_context)
    monkeypatch.setattr(worker, "mark_running", fake_noop)
    monkeypatch.setattr(worker, "mark_finished", fake_noop)
    monkeypatch.setattr(worker, "_set_phase", fake_noop)
    monkeypatch.setattr(worker, "is_cancelled", never_cancelled)
    monkeypatch.setattr(worker, "append_event", fake_append)

    await worker.run_one_parliament(
        {
            "id": "p-waiting",
            "topic": "要不要买房",
            "topic_category": "real_estate",
            "user_background": "",
            "user_id": None,
            "device_id": "d1",
            "selected_expert_ids": [],
            "clarification_status": "not_checked",
            "clarification_questions": [],
            "clarification_answers": [],
            "transcript": [],
        }
    )

    assert "parliament_done" not in events
    assert "node_complete" in events
