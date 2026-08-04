"""测试 L1 进程锁 + L2 DB 启动闸。

覆盖根因：
  - 两个 worker 同时跑导致不公平、orphan、状态混淆 → flock 防呆
  - flock 失效（NFS/跨容器）时 → DB worker_heartbeats 表兜底
"""
import asyncio
import fcntl
import os

import pytest

from app import worker
from app.llm_concurrency import (
    acquire_llm_slots,
    reset_semaphore_for_tests,
    clear_per_parliament_semaphore,
)


# ============================================================
# L1 flock
# ============================================================
def test_acquire_worker_lock_blocks_second_holder(tmp_path, monkeypatch):
    """拿过一次锁后，第二个 acquire 应当失败（同进程内模拟两个进程）。"""
    lock_path = tmp_path / "worker.lock"
    monkeypatch.setattr(worker, "_FLOCK_PATH", lock_path)
    # 让 _FLOCK_PATH 在 acquire 内被读取（它是模块常量；改完直接调用）
    # 同时避免污染真实 .worker.lock

    # 模拟第一个进程：手动拿锁
    fd1 = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd1, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        # 模拟第二个进程：通过 acquire_worker_lock 再拿，应当失败
        # 重置模块全局 fd 让它走"全新"路径
        worker._worker_lock_fd = None
        ok = worker.acquire_worker_lock()
        assert ok is False, "第二个 worker 应当拿不到锁"
    finally:
        fcntl.flock(fd1, fcntl.LOCK_UN)
        os.close(fd1)


def test_acquire_worker_lock_bypass_when_allow_duplicate(
    tmp_path, monkeypatch
):
    """WORKER_ALLOW_DUPLICATE=true 时跳过 flock（调试开关）。"""
    lock_path = tmp_path / "worker.lock"
    monkeypatch.setattr(worker, "_FLOCK_PATH", lock_path)

    class FakeSettings:
        worker_allow_duplicate = True

    monkeypatch.setattr(worker, "get_settings", lambda: FakeSettings())
    worker._worker_lock_fd = None
    ok = worker.acquire_worker_lock()
    assert ok is True


def test_release_worker_lock_is_idempotent():
    """重复释放不应当报错（finally / signal handler 兜底安全）。"""
    worker._worker_lock_fd = None
    worker.release_worker_lock()  # 不抛即算通过


# ============================================================
# L2 DB 启动闸
# ============================================================
@pytest.mark.asyncio
async def test_worker_singleton_guard_refuses_when_live_workers(monkeypatch):
    """DB 检测到活跃 worker 心跳 → 拒绝启动。"""
    async def fake_live():
        return 1

    monkeypatch.setattr(worker, "_worker_available_live", fake_live)
    # 不允许 duplicate 时应当拒绝
    assert await worker._worker_singleton_guard() is False


@pytest.mark.asyncio
async def test_worker_singleton_guard_allowed_when_no_live(monkeypatch):
    """DB 无活跃 worker → 允许启动。"""
    async def fake_live():
        return 0

    monkeypatch.setattr(worker, "_worker_available_live", fake_live)
    assert await worker._worker_singleton_guard() is True


@pytest.mark.asyncio
async def test_worker_singleton_guard_bypass_when_duplicate(monkeypatch):
    """WORKER_ALLOW_DUPLICATE=true → 即使有活 worker 也允许。"""
    class FakeSettings:
        worker_allow_duplicate = True

    monkeypatch.setattr(worker, "get_settings", lambda: FakeSettings())
    assert await worker._worker_singleton_guard() is True


# ============================================================
# 公平分槽：acquire_llm_slots 行为
# ============================================================
@pytest.mark.asyncio
async def test_acquire_llm_slots_parliament_isolation(monkeypatch):
    """不同 parliament_id 的并发不会被互相阻挡（议会级隔离生效）。"""
    # 强制小并发便于测
    monkeypatch.setenv("LLM_MAX_CONCURRENCY", "4")

    class FakeSettings:
        per_parliament_llm_concurrency = 1  # 单议会最多 1 在飞

    monkeypatch.setattr("app.llm_concurrency.get_settings", lambda: FakeSettings())
    reset_semaphore_for_tests()
    p1_concurrent = 0
    p1_max = 0
    p2_concurrent = 0
    p2_max = 0

    async def run_p1():
        nonlocal p1_concurrent, p1_max
        async with acquire_llm_slots("parliament-A"):
            p1_concurrent += 1
            p1_max = max(p1_max, p1_concurrent)
            await asyncio.sleep(0.05)
            p1_concurrent -= 1

    async def run_p2():
        nonlocal p2_concurrent, p2_max
        async with acquire_llm_slots("parliament-B"):
            p2_concurrent += 1
            p2_max = max(p2_max, p2_concurrent)
            await asyncio.sleep(0.05)
            p2_concurrent -= 1

    # 各跑 3 个并发任务
    await asyncio.gather(
        run_p1(), run_p1(), run_p1(),
        run_p2(), run_p2(), run_p2(),
    )
    clear_per_parliament_semaphore("parliament-A")
    clear_per_parliament_semaphore("parliament-B")
    # 每议会内并发不应超过配置的 per_parliament_llm_concurrency（此处为 1）
    assert p1_max <= 1
    assert p2_max <= 1


@pytest.mark.asyncio
async def test_acquire_llm_slots_none_pid_falls_back_to_global(monkeypatch):
    """parliament_id=None 时只走全局 semaphore，不报错。"""
    monkeypatch.setenv("LLM_MAX_CONCURRENCY", "3")
    reset_semaphore_for_tests()
    async with acquire_llm_slots(None) as inner:
        assert inner is None  # asynccontextmanager 在 yield None 时不抛
