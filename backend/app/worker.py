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
import json
import logging
import re
import time

import psycopg

from app.config import get_settings
from app.event_store import append_event
from app.graph.parliament import get_graph_with_checkpointer
from app.graph.state import make_initial_state
from app.runtime_context import (
    get_or_create_context,
    is_cancelled,
    mark_finished,
    mark_running,
)

logger = logging.getLogger(__name__)

# 单进程同时跑几场议会
_MAX_CONCURRENT = 2


def _clean_db_url() -> str:
    return re.sub(r"\?.*$", "", get_settings().supabase_db_url)


async def fetch_next_queued_parliament() -> dict | None:
    """SELECT FOR UPDATE SKIP LOCKED 抢一个 queued 任务。

    多 worker 并发安全：Postgres 保证同一行不会被两个 worker 同时抢。
    """
    url = _clean_db_url()

    def _fetch():
        with psycopg.connect(url, connect_timeout=30) as conn:
            with conn.cursor() as cur:
                # set short lock timeout, SKIP LOCKED 避免阻塞
                cur.execute("SET LOCAL lock_timeout = '3s'")
                cur.execute("""
                    SELECT id, topic, topic_category, user_background, user_id, device_id
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
                cols = ["id", "topic", "topic_category", "user_background", "user_id", "device_id"]
                task = dict(zip(cols, row))
                # 标记为 running
                cur.execute(
                    """UPDATE parliaments
                       SET status = 'running', started_at = now()
                       WHERE id = %s""",
                    (task["id"],),
                )
                conn.commit()
                return task

    return await asyncio.to_thread(_fetch)


def _build_initial_state(task: dict) -> dict:
    """从 DB 行构造 LangGraph initial state。"""
    return make_initial_state(
        topic=task["topic"],
        user_id=task.get("user_id"),
        topic_category=task.get("topic_category"),
        user_background=task.get("user_background"),
        parliament_id=str(task["id"]),
    ) | {"started_at": time.time()}


def _safe_serialize(obj) -> dict:
    """LangGraph state_update 的安全序列化（不可 JSON 的转 str）。"""
    from app.event_store import _make_safe
    return _make_safe(obj) if isinstance(obj, dict) else {"value": str(obj)}


async def run_one_parliament(task: dict) -> None:
    """跑完一场议会。所有异常都被捕获，确认 status 写回 DB。"""
    parliament_id = str(task["id"])
    settings = get_settings()
    total_timeout = settings.parliament_total_timeout

    logger.info("Worker 开始跑议会 %s topic=%r", parliament_id, task.get("topic", "")[:60])

    # 标 parliament_runtime 为 running
    await get_or_create_context(parliament_id)
    await mark_running(parliament_id)

    start_ts = time.time()
    append_event(parliament_id, "parliament_started", {
        "topic": task["topic"],
        "category": task.get("topic_category"),
    })

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
            elapsed = time.time() - start_ts
            if elapsed > total_timeout:
                logger.warning("议会 %s 超时（%ds）", parliament_id, int(elapsed))
                from app.cancel_guard import cancel_all_inflight
                await cancel_all_inflight(parliament_id)
                append_event(parliament_id, "error", {
                    "error": f"议会超时（{total_timeout}s 强制终止）",
                })
                break

            # cancel 检查
            if await is_cancelled(parliament_id):
                logger.info("议会 %s 被用户取消", parliament_id)
                from app.cancel_guard import cancel_all_inflight
                await cancel_all_inflight(parliament_id)
                append_event(parliament_id, "cancelled", {})
                break

            # 推送 node_complete
            for node_name, state_update in event.items():
                try:
                    payload = _safe_serialize(state_update)
                except Exception:
                    payload = {"error": "serialize_failed"}
                append_event(parliament_id, "node_complete", {
                    "node": node_name,
                    "payload": payload,
                })

        # 成功完成
        append_event(parliament_id, "parliament_done", {})
        logger.info("议会 %s 完成", parliament_id)

    except asyncio.CancelledError:
        logger.warning("议会 %s 被中断", parliament_id)
        append_event(parliament_id, "error", {"error": "worker interrupted"})
        raise
    except Exception as e:
        logger.exception("议会 %s 执行失败: %s", parliament_id, e)
        append_event(parliament_id, "error", {
            "error": f"parliament execution error: {e}",
        })
    finally:
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
    reclaimed = await _reclaim_stale_tasks()
    if reclaimed > 0:
        logger.info("启动回收 %d 个卡死任务", reclaimed)

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
    running_tasks: set[asyncio.Task] = set()

    logger.info("Worker 启动，并发上限 %d", _MAX_CONCURRENT)

    while True:
        # 清理已完成的子 task
        done = {t for t in running_tasks if t.done()}
        running_tasks -= done

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


def main() -> None:
    """sync 入口（python -m app.worker 调）。"""
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
