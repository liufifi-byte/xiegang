"""P0-S2.2: Worker 主循环——独立进程跑 LangGraph，不依赖 SSE。

启动方式：
    python -m app.worker         # 单进程

部署：
    Railway 里第二个服务，start command = python -m app.worker

工作流程：
    loop:
        1. SELECT FOR UPDATE SKIP LOCKED 抢一个 status='queued' 的议会
        2. UPDATE status='running', started_at=now()
        3. 跑 LangGraph 6 节点（asyncio, cancel_guard 防烧）
        4. 每节点完成 → append_event('node_complete', payload)
        5. cancel 信号检查
        6. 全图完成 → status='done' + append_event('parliament_done')
           异常 → status='failed' + append_event('error')

并发控制：asyncio.Semaphore(N) 单进程同时跑 N 场议会。
"""
import asyncio
import contextlib
import logging
import re
import time
import uuid
from datetime import datetime, timezone

import psycopg

from app.config import get_settings
from app.db import get_supabase
from app.event_store import append_event
from app.graph.parliament import get_graph_with_checkpointer
from app.graph.state import make_initial_state
from app.runtime_context import (
    get_or_create_context,
    is_cancelled,
    mark_finished,
    mark_running,
    request_cancel,
)

logger = logging.getLogger(__name__)

# 单进程同时跑几场议会
_MAX_CONCURRENT = 2
_WORKER_ID = f"worker-{uuid.uuid4().hex[:10]}"


async def _cancel_when_requested(
    parliament_id: str,
    target_task: asyncio.Task,
    *,
    poll_seconds: float = 1.0,
) -> None:
    """Bridge the DB cancel flag into the separate worker process."""
    while not target_task.done():
        await asyncio.sleep(poll_seconds)
        try:
            if await is_cancelled(parliament_id):
                target_task.cancel()
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("议会 %s 取消状态检查失败: %s", parliament_id, exc)


def _clean_db_url() -> str:
    return re.sub(r"\?.*$", "", get_settings().supabase_db_url)


async def _update_worker_heartbeat(active_count: int) -> None:
    """Publish liveness so queued users never stare at an ambiguous spinner."""
    def update() -> None:
        with psycopg.connect(_clean_db_url(), connect_timeout=30) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO worker_heartbeats
                        (worker_id, last_seen_at, capacity, active_count)
                    VALUES (%s, now(), %s, %s)
                    ON CONFLICT (worker_id) DO UPDATE
                    SET last_seen_at = now(),
                        capacity = excluded.capacity,
                        active_count = excluded.active_count
                    """,
                    (_WORKER_ID, _MAX_CONCURRENT, active_count),
                )
            conn.commit()
    try:
        await asyncio.to_thread(update)
    except Exception as exc:
        logger.warning("worker heartbeat 写入失败: %s", exc)


async def _set_phase(parliament_id: str, phase: str) -> None:
    def update() -> None:
        (
            get_supabase()
            .table("parliaments")
            .update({"current_phase": phase})
            .eq("id", parliament_id)
            .execute()
        )
    try:
        await asyncio.to_thread(update)
    except Exception as exc:
        logger.warning("议会 %s 阶段写入失败: %s", parliament_id, exc)


async def _cancel_abandoned_councils() -> int:
    """Cancel councils whose browser lease expired, with refresh grace.

    P0 fix: 只取消 last_client_heartbeat 被设过且超过 60s 没刷新的议会。
    POST 创建时不写 heartbeat（只在 SSE 连上后才写），避免刚创建就被误取消。
    超时从 20s 放到 60s——给网络抖动留余地。
    """
    def find() -> list[str]:
        with psycopg.connect(_clean_db_url(), connect_timeout=30) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM parliaments
                    WHERE status IN ('queued', 'running')
                      AND last_client_heartbeat IS NOT NULL
                      AND last_client_heartbeat < now() - interval '60 seconds'
                      AND (
                        keep_alive_until IS NULL
                        OR keep_alive_until < now()
                      )
                    """
                )
                return [str(row[0]) for row in cur.fetchall()]

    ids = await asyncio.to_thread(find)
    for parliament_id in ids:
        await get_or_create_context(parliament_id)
        await request_cancel(parliament_id)
        await _mark_terminal_status(
            parliament_id, "cancelled", "client_lease_expired"
        )
        _append_event_safe(
            parliament_id, "cancelled", {"reason": "client_lease_expired"}
        )
    return len(ids)


async def fetch_next_queued_parliament() -> dict | None:
    """SELECT FOR UPDATE SKIP LOCKED 抢一个 queued 任务。

    多 worker 并发安全：Postgres 保证同一行不会被两个 worker 同时抢。
    网络抖动时返回 None 让 loop 重试。
    """
    url = _clean_db_url()

    def _fetch():
        with psycopg.connect(url, connect_timeout=30) as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = '3s'")
                cur.execute("""
                    SELECT id, topic, topic_category, user_background, user_id, device_id,
                           selected_expert_ids
                    FROM parliaments
                    WHERE status = 'queued'
                    ORDER BY created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                """)
                row = cur.fetchone()
                if row is None:
                    conn.rollback()
                    return None
                cols = [
                    "id",
                    "topic",
                    "topic_category",
                    "user_background",
                    "user_id",
                    "device_id",
                    "selected_expert_ids",
                ]
                task = dict(zip(cols, row))
                cur.execute(
                    """UPDATE parliaments
                       SET status = 'running',
                           current_phase = 'claimed',
                           started_at = now()
                       WHERE id = %s""",
                    (task["id"],),
                )
                conn.commit()
                return task

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        logger.warning("fetch_next_queued_parliament 失败（网络抖动?）: %s", e)
        return None


def _build_initial_state(task: dict) -> dict:
    """从 DB 行构造 LangGraph initial state。"""
    return make_initial_state(
        topic=task["topic"],
        user_id=task.get("user_id"),
        topic_category=task.get("topic_category"),
        user_background=task.get("user_background"),
        parliament_id=str(task["id"]),
        selected_expert_ids=task.get("selected_expert_ids"),
    ) | {"started_at": time.time()}


def _safe_serialize(obj) -> dict:
    """LangGraph state_update 的安全序列化（不可 JSON 的转 str）。"""
    from app.event_store import _make_safe
    return _make_safe(obj) if isinstance(obj, dict) else {"value": str(obj)}


def _append_event_safe(
    parliament_id: str,
    event_type: str,
    payload: dict,
) -> bool:
    """Telemetry is best effort and must never terminate the council run."""
    try:
        append_event(parliament_id, event_type, payload)
        return True
    except Exception as exc:
        logger.warning(
            "议会 %s 写入 %s 事件失败，主流程继续: %s",
            parliament_id,
            event_type,
            exc,
        )
        return False


async def _mark_terminal_status(
    parliament_id: str,
    status: str,
    error_message: str | None = None,
) -> None:
    """Persist terminal status through the HTTP API when direct DB telemetry fails."""

    def update() -> None:
        from app.db import get_supabase

        payload = {
            "status": status,
            "current_phase": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error_message": error_message[:500] if error_message else None,
        }
        (
            get_supabase()
            .table("parliaments")
            .update(payload)
            .eq("id", parliament_id)
            .execute()
        )

    try:
        await asyncio.to_thread(update)
    except Exception as exc:
        logger.warning(
            "议会 %s 写入终态 %s 失败: %s", parliament_id, status, exc
        )


async def run_one_parliament(task: dict) -> None:
    """跑完一场议会。所有异常都被捕获，确认 status 写回 DB。"""
    parliament_id = str(task["id"])
    settings = get_settings()
    total_timeout = settings.parliament_total_timeout

    logger.info("Worker 开始跑议会 %s topic=%r", parliament_id, task.get("topic", "")[:60])

    # 标 parliament_runtime 为 running
    await get_or_create_context(parliament_id)
    await mark_running(parliament_id)
    current_task = asyncio.current_task()
    cancel_watcher = (
        asyncio.create_task(
            _cancel_when_requested(parliament_id, current_task)
        )
        if current_task is not None
        else None
    )

    start_ts = time.monotonic()
    terminated_early = False
    _append_event_safe(parliament_id, "parliament_started", {
        "topic": task["topic"],
        "category": task.get("topic_category"),
    })
    await _set_phase(parliament_id, "claimed")

    try:
        graph = await get_graph_with_checkpointer()
        config = {"configurable": {"thread_id": parliament_id}}

        # 自动续盘：检查 checkpoint
        try:
            snapshot = await graph.aget_state(config)
            has_checkpoint = snapshot is not None and snapshot.next
        except Exception:
            has_checkpoint = False

        state = _build_initial_state(task)
        run_input = None if has_checkpoint else state

        async for event in graph.astream(run_input, config=config):
            # 超时检查
            elapsed = time.monotonic() - start_ts
            if elapsed > total_timeout:
                logger.warning("议会 %s 超时（%ds）", parliament_id, int(elapsed))
                from app.cancel_guard import cancel_all_inflight
                await cancel_all_inflight(parliament_id)
                _append_event_safe(parliament_id, "error", {
                    "error": f"议会超时（{total_timeout}s 强制终止）",
                })
                await _mark_terminal_status(
                    parliament_id,
                    "failed",
                    f"parliament_timeout_{total_timeout}s",
                )
                terminated_early = True
                break

            # cancel 检查
            if await is_cancelled(parliament_id):
                logger.info("议会 %s 被用户取消", parliament_id)
                from app.cancel_guard import cancel_all_inflight
                await cancel_all_inflight(parliament_id)
                _append_event_safe(parliament_id, "cancelled", {})
                await _mark_terminal_status(
                    parliament_id, "cancelled", "cancelled_by_user"
                )
                terminated_early = True
                break

            # 推送 node_complete
            for node_name, state_update in event.items():
                phase_by_node = {
                    "parse_topic": "claimed",
                    "select_experts": "researching",
                    "research": "researching",
                    "opening_round": "cross_examining",
                    "extract_disputes": "cross_examining",
                    "cross_examination_round": "cross_examining",
                    "assess_third_round": "cross_examining",
                    "convergence_round": "cross_examining",
                    "summary": "summarizing",
                    "archive": "done",
                }
                if node_name in phase_by_node:
                    await _set_phase(parliament_id, phase_by_node[node_name])
                try:
                    payload = _safe_serialize(state_update)
                except Exception:
                    payload = {"error": "serialize_failed"}
                _append_event_safe(parliament_id, "node_complete", {
                    "node": node_name,
                    "payload": payload,
                })

        if not terminated_early:
            _append_event_safe(parliament_id, "parliament_done", {})
            logger.info("议会 %s 完成", parliament_id)

    except asyncio.CancelledError:
        requested = False
        try:
            requested = await is_cancelled(parliament_id)
        except Exception:
            pass
        if requested:
            logger.info("议会 %s 已响应用户取消", parliament_id)
            _append_event_safe(parliament_id, "cancelled", {})
            await _mark_terminal_status(
                parliament_id, "cancelled", "cancelled_by_user"
            )
            terminated_early = True
        else:
            logger.warning("议会 %s 被工作进程中断", parliament_id)
            _append_event_safe(
                parliament_id, "error", {"error": "worker interrupted"}
            )
            await _mark_terminal_status(
                parliament_id, "failed", "worker_interrupted"
            )
            raise
    except Exception as e:
        logger.exception("议会 %s 执行失败: %s", parliament_id, e)
        _append_event_safe(parliament_id, "error", {
            "error": f"parliament execution error: {e}",
        })
        await _mark_terminal_status(
            parliament_id,
            "failed",
            f"{type(e).__name__}: {e}",
        )
    finally:
        if cancel_watcher is not None:
            cancel_watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_watcher
        await mark_finished(parliament_id)


async def _reclaim_stale_tasks() -> int:
    """启动扫描：把 running 但超期的标 failed。"""
    url = _clean_db_url()

    def _do():
        with psycopg.connect(url, connect_timeout=30) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE parliaments
                    SET status = 'failed',
                        error_message = 'worker_restart_reclaim',
                        finished_at = now()
                    WHERE status = 'running'
                      AND finished_at IS NULL
                      AND started_at < now() - interval '15 minutes'
                    RETURNING id
                """)
                rows = cur.fetchall()
                conn.commit()
                return len(rows)
    return await asyncio.to_thread(_do)


async def worker_loop() -> None:
    """主循环。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [worker] %(levelname)s %(name)s: %(message)s",
    )

    # 启动时回收卡死任务
    try:
        reclaimed = await _reclaim_stale_tasks()
        if reclaimed > 0:
            logger.info("启动回收 %d 个卡死任务", reclaimed)
    except Exception as e:
        logger.warning("启动回收失败（继续）: %s", e)

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
    running_tasks: set[asyncio.Task] = set()

    logger.info("Worker 启动，并发上限 %d", _MAX_CONCURRENT)
    last_lease_sweep = 0.0

    while True:
        try:
            # 清理已完成的子 task
            done = {t for t in running_tasks if t.done()}
            running_tasks -= done
            await _update_worker_heartbeat(len(running_tasks))
            if time.monotonic() - last_lease_sweep >= 5:
                cancelled = await _cancel_abandoned_councils()
                if cancelled:
                    logger.info("已取消 %d 场离站会议", cancelled)
                last_lease_sweep = time.monotonic()

            # 并发未满才抢新任务
            if len(running_tasks) >= _MAX_CONCURRENT:
                await asyncio.sleep(1)
                continue

            task = await fetch_next_queued_parliament()
            if task is None:
                await asyncio.sleep(2)
                continue

            logger.info("抢到任务 parliament_id=%s", task["id"])

            async def _run_with_semaphore(t):
                async with semaphore:
                    await run_one_parliament(t)

            new_task = asyncio.ensure_future(_run_with_semaphore(task))
            running_tasks.add(new_task)

        except Exception as e:
            # P0-S2 fix: 任何 loop 迭代里的异常都不能让 worker 进程崩
            logger.exception("worker_loop 迭代异常: %s", e)
            await asyncio.sleep(5)  # 5s 退避，避免狂打 DB


def main() -> None:
    """sync 入口（python -m app.worker 调）。"""
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
