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
import fcntl
import hashlib
import logging
import os
import re
import signal
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

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
_MAX_CONCURRENT = 5

# ============================================================
# 进程防呆：stable worker id（hostname + flock 路径 hash）
# ============================================================
_FLOCK_PATH = Path(os.environ.get(
    "WORKER_LOCK_PATH",
    str(Path(__file__).resolve().parent.parent / ".worker.lock"),
))
# 全局 flock 文件句柄（被 main() 拿住后保持打开，进程退出时自动释放）
_worker_lock_fd: "int | None" = None


def _stable_worker_id() -> str:
    """稳定 worker id：同机器上多进程时，能看到彼此在 DB 里的心跳。

    依据 hostname + flock 路径生成 hash 前缀；保留 uuid 后缀以区分同机多调试实例。
    """
    host = socket.gethostname() or "host"
    digest = hashlib.sha1(f"{host}:{_FLOCK_PATH}".encode()).hexdigest()[:10]
    return f"worker-{digest}-{uuid.uuid4().hex[:6]}"


# 改成稳定 id（不再每次纯 uuid）
_WORKER_ID = _stable_worker_id()


# ============================================================
# L1 进程锁：fcntl.flock 非阻塞抢占
# ============================================================
def acquire_worker_lock() -> bool:
    """同机器 worker 单例保护。返回 True=拿到锁，False=已有 worker 在跑。

    用 fcntl.LOCK_EX | LOCK_NB 非阻塞：拿不上立刻返回而不会阻塞进程。
    flock 在 NFS / 跨容器上可能失效——L2 DB heartbeat 兜底。
    """
    if get_settings().worker_allow_duplicate:
        logger.warning("WORKER_ALLOW_DUPLICATE=true，跳过 flock 防呆（仅调试用）")
        return True

    global _worker_lock_fd
    try:
        _FLOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_FLOCK_PATH), os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # 写 pid 进去便于排查
        os.write(fd, f"{os.getpid()}\n".encode())
        os.lseek(fd, 0, os.SEEK_SET)
        _worker_lock_fd = fd
        return True
    except (BlockingIOError, OSError) as exc:
        logger.error(
            "已有 worker 在跑（flock 抢占失败: %s）。"
            "如确定要起，请先 kill 旧 worker 或删除 %s。"
            "调试可设 WORKER_ALLOW_DUPLICATE=true 绕过。",
            exc, _FLOCK_PATH,
        )
        return False


def release_worker_lock() -> None:
    """进程退出时释放 flock + 删除 DB heartbeat。幂等。"""
    global _worker_lock_fd
    if _worker_lock_fd is not None:
        try:
            fcntl.flock(_worker_lock_fd, fcntl.LOCK_UN)
            os.close(_worker_lock_fd)
        except OSError:
            pass
        _worker_lock_fd = None
    # 删 DB heartbeat 行
    try:
        with psycopg.connect(_clean_db_url(), connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM worker_heartbeats WHERE worker_id = %s",
                    (_WORKER_ID,),
                )
            conn.commit()
    except Exception:
        pass


async def _worker_available_live() -> int:
    """检查 DB 里有多少个活的 worker 心跳。供 L2 启动闸用。"""
    def _do() -> int:
        with psycopg.connect(_clean_db_url(), connect_timeout=10) as conn:
            with conn.cursor() as cur:
                # 排除自己（理论上启动时还没写心跳，但保险）
                cur.execute(
                    """
                    SELECT COUNT(*) FROM worker_heartbeats
                    WHERE last_seen_at >= now() - interval '15 seconds'
                      AND worker_id <> %s
                    """,
                    (_WORKER_ID,),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
    try:
        return await asyncio.to_thread(_do)
    except Exception as exc:
        logger.warning("worker_heartbeats 检查失败（降级放行）: %s", exc)
        return 0


async def _purge_zombie_heartbeats() -> int:
    """清掉所有 >15s 未刷新的 heartbeat 行。

    这些是被 SIGKILL 或异常退出时未走 release_worker_lock 留下的 zombie。
    防止它们永久挡住下一个 worker 启动（L2 启动闸只看 15s 内的行）。
    """
    def _do() -> int:
        with psycopg.connect(_clean_db_url(), connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM worker_heartbeats
                    WHERE last_seen_at < now() - interval '15 seconds'
                    """
                )
                cnt = cur.rowcount
            conn.commit()
            return cnt
    try:
        return await asyncio.to_thread(_do)
    except Exception as exc:
        logger.warning("zombie heartbeat 清理失败（继续）: %s", exc)
        return 0


async def _worker_singleton_guard() -> bool:
    """L2 启动闸：DB 已有活 worker → 拒绝启动。返回 True=允许启动。

    副作用：先清掉过期 zombie heartbeat，再判断；
    这样新 worker 不会被上一个异常退出 worker 的过期心跳挡死。
    """
    if get_settings().worker_allow_duplicate:
        return True
    await _purge_zombie_heartbeats()
    live = await _worker_available_live()
    if live > 0:
        logger.error(
            "DB 检测到 %d 个活 worker 心跳，拒绝启动新 worker。"
            "如确定要起：检查 worker_heartbeats 表是否有过期行未清理；"
            "调试可设 WORKER_ALLOW_DUPLICATE=true。",
            live,
        )
        return False
    return True


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


def _direct_db_url() -> str:
    """Worker 用的 DB URL。详见 app.db.direct_db_url()。"""
    from app.db import direct_db_url
    return direct_db_url()


# 兼容旧调用点
_clean_db_url = _direct_db_url


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
    """Cancel councils whose browser lease expired.

    P0 fix: 关闭页面 → SSE 断开 → 15s 内无新 heartbeat → 立即取消。
    比之前 60s 更激进——用户关闭页面就停（省 token）。
    SSE 短暂断开（刷新/网络抖动）有 15s 恢复窗口。
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
                      AND last_client_heartbeat < now() - interval '15 seconds'
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
                           selected_expert_ids, clarification_status,
                           clarification_questions, clarification_answers,
                           decision_path, decision_path_status,
                           idempotency_key,
                           transcript
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
                    "clarification_status",
                    "clarification_questions",
                    "clarification_answers",
                    "decision_path",
                    "decision_path_status",
                    "idempotency_key",
                    "transcript",
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
    """从 DB 行构造 LangGraph initial state。

    PRD v1.1 §14.3: 把 decision_path / decision_path_status 一并塞进 state,
    parse_topic 节点会优先读这两字段(不走 LLM 重新分类 / 不预填 research_questions)。
    """
    return make_initial_state(
        topic=task["topic"],
        user_id=task.get("user_id"),
        topic_category=task.get("topic_category"),
        user_background=task.get("user_background"),
        parliament_id=str(task["id"]),
        selected_expert_ids=task.get("selected_expert_ids"),
        clarification_status=task.get("clarification_status") or "not_checked",
        clarification_questions=task.get("clarification_questions") or [],
        clarification_answers=task.get("clarification_answers") or [],
        decision_path=task.get("decision_path") or {},
        decision_path_status=task.get("decision_path_status") or "not_started",
    ) | {
        "started_at": time.time(),
        "transcript": list(task.get("transcript") or []),
    }


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
    finally:
        # 释放本议会的 per-parliament LLM semaphore 缓存（公平分槽清理）
        # 所有终态路径都走这里：done / failed / cancelled，统一兜底
        try:
            from app.llm_concurrency import clear_per_parliament_semaphore
            clear_per_parliament_semaphore(parliament_id)
        except Exception:
            pass


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
    waiting_for_input = False
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
                    "select_experts": "claimed",
                    "clarification_gate": "claimed",
                    "persist_clarification": "awaiting_input",
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
                if (
                    node_name == "persist_clarification"
                    and state_update.get("clarification_waiting")
                ):
                    waiting_for_input = True
                try:
                    payload = _safe_serialize(state_update)
                except Exception:
                    payload = {"error": "serialize_failed"}
                _append_event_safe(parliament_id, "node_complete", {
                    "node": node_name,
                    "payload": payload,
                })

        if waiting_for_input:
            logger.info("议会 %s 等待用户补充，Worker 已释放", parliament_id)
        elif not terminated_early:
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
    """L3 orphan 自愈：把 running 但超期无心跳的议会标 failed。

    阈值由 settings.parliament_stale_seconds 控制（默认 120s，原硬编码 900s）。
    保留 status='running' AND finished_at IS NULL 双重过滤，避免误改历史完成/取消的议会。
    """
    url = _clean_db_url()
    threshold = get_settings().parliament_stale_seconds

    def _do():
        with psycopg.connect(url, connect_timeout=30) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE parliaments
                    SET status = 'failed',
                        error_message = 'worker_restart_reclaim',
                        finished_at = now()
                    WHERE status = 'running'
                      AND finished_at IS NULL
                      AND started_at < now() - (%s || ' seconds')::interval
                    RETURNING id
                    """,
                    (str(threshold),),
                )
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

    # L2 启动闸：DB 已有活 worker → 拒绝启动
    if not await _worker_singleton_guard():
        return  # 拒绝启动（main() 的 finally 会释放 flock + 删 heartbeat）

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
    last_orphan_sweep = 0.0
    orphan_sweep_interval = 30.0  # L3 主 loop 内每 30s 主动 reclaim orphan 议会

    try:
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

                # L3 主 loop 内定时 reclaim orphan 议会（不只启动时跑）
                if time.monotonic() - last_orphan_sweep >= orphan_sweep_interval:
                    try:
                        reclaimed = await _reclaim_stale_tasks()
                        if reclaimed > 0:
                            logger.info(
                                "主 loop 回收 %d 个 orphan 议会（>stale 阈值）",
                                reclaimed,
                            )
                    except Exception as exc:
                        logger.warning("主 loop orphan sweep 失败（继续）: %s", exc)
                    last_orphan_sweep = time.monotonic()

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
    finally:
        # 进程退出（含 SIGTERM/SIGINT 触发的 CancelledError）时清理
        # 正在跑的 task 留给 _reclaim_stale_tasks 在下次启动或本机其他 worker 处理
        logger.info("Worker 退出，开始清理：删 DB heartbeat + 释放 flock")
        try:
            # 尽量取消正在跑的议会（让它们走 mark_finished 路径，
            # 不行的会被新的 reclaim 逻辑在 120s 内清掉）
            for t in list(running_tasks):
                if not t.done():
                    t.cancel()
            # 给已取消任务一点时间走完 finally
            if running_tasks:
                await asyncio.sleep(0.5)
        except Exception:
            pass


def main() -> None:
    """sync 入口（python -m app.worker 调）。"""
    # L1 进程锁：同机器只允许一个 worker
    if not acquire_worker_lock():
        # 退出码 2 表示"已有进程在跑"，便于 scripts/dev.sh 判断
        raise SystemExit(2)

    # 注册信号 handler：SIGTERM/SIGINT 触发时释放资源
    def _on_signal(signum, _frame):
        logger.warning("收到信号 %s，开始清理后退出", signum)
        release_worker_lock()
        # asyncio.run 正在跑的话 CancelledError 会让 worker_loop finally 跑
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            # 非 main thread 或 Windows 不全支持，忽略
            pass

    try:
        asyncio.run(worker_loop())
    finally:
        # 兜底清理（无论正常结束、信号、异常）
        release_worker_lock()


if __name__ == "__main__":
    main()
