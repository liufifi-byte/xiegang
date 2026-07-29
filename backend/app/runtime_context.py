"""运行时上下文：每个议会一份，跨请求共享。

V1: 进程内 Dict（多 worker 不共享）
V2-S1: 改为 Postgres-backed，DB 是 source of truth
      保留同接口签名（consume_user_interrupt / set_user_interrupt / cancel 等）
      测试时通过环境变量 SKIP_DB_CONTEXT=true 退回 in-memory

V1-S4 的 cancel_requested 字段在这里。
"""
import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import psycopg

from app.db import direct_db_url


@dataclass
class ParliamentRuntimeContext:
    """兼容性 dataclass——V2 里实际状态在 DB。"""
    parliament_id: str
    user_interrupt: str | None = None
    cancel_requested: bool = False
    is_running: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


# ============ DB helpers ============

def _clean_db_url(url: str = "") -> str:
    """统一走 app.db.direct_db_url()（绕过 Supabase pooler）。

    保留 url 形参仅为兼容旧调用点；新代码直接用 direct_db_url()。
    """
    return direct_db_url()


def _skip_db() -> bool:
    """测试环境或 DB 不可用时，用 in-memory 退路。"""
    return os.environ.get("SKIP_DB_CONTEXT", "").lower() in ("1", "true", "yes")


# ============ in-memory fallback（测试 / DB 不可用时）============

_CONTEXTS: dict[str, ParliamentRuntimeContext] = {}
_LOCK = asyncio.Lock()


# ============ DB-backed 实现 ============

async def _db_upsert(parliament_id: str, **fields) -> None:
    """插入或更新一行。空表时 upsert。"""
    url = _clean_db_url()
    columns = ["parliament_id"] + list(fields.keys())
    placeholders = ["%s"] * len(columns)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in fields.keys())
    sql = f"""
        INSERT INTO parliament_runtime ({", ".join(columns)})
        VALUES ({", ".join(placeholders)})
        ON CONFLICT (parliament_id) DO UPDATE SET {updates}
    """
    # 用 asyncio.to_thread 让 psycopg 同步调用不阻塞事件循环
    await asyncio.to_thread(_exec_sql, sql, url, [parliament_id] + list(fields.values()))


def _exec_sql(sql: str, url: str, params: list) -> None:
    with psycopg.connect(url, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()


def _fetch_one(sql: str, params: list) -> dict | None:
    url = _clean_db_url()
    with psycopg.connect(url, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                return None
            cols = [desc[0] for desc in cur.description]
            return dict(zip(cols, row))


def _db_fetch(parliament_id: str) -> dict | None:
    return _fetch_one(
        "SELECT * FROM parliament_runtime WHERE parliament_id = %s",
        [parliament_id],
    )


# ============ 公共接口（签名与 V1 完全一致）============

async def get_or_create_context(parliament_id: str) -> ParliamentRuntimeContext:
    """获取或创建某个议会的运行时上下文。

    DB 模式下：if 不存在则 upsert 一行空记录后 fetch。
    In-memory 模式下：直接维护 Dict。
    """
    if _skip_db():
        async with _LOCK:
            if parliament_id not in _CONTEXTS:
                _CONTEXTS[parliament_id] = ParliamentRuntimeContext(parliament_id=parliament_id)
            return _CONTEXTS[parliament_id]

    row = await asyncio.to_thread(_db_fetch, parliament_id)
    if row is None:
        now = datetime.now(timezone.utc)
        await _db_upsert(
            parliament_id,
            user_interrupt=None,
            cancel_requested=False,
            is_running=False,
            started_at=now,
            finished_at=None,
        )
        row = await asyncio.to_thread(_db_fetch, parliament_id)
    # 转 dataclass 兼容老调用
    return ParliamentRuntimeContext(
        parliament_id=parliament_id,
        user_interrupt=row.get("user_interrupt"),
        cancel_requested=row.get("cancel_requested", False),
        is_running=row.get("is_running", False),
        started_at=row.get("started_at") or datetime.now(timezone.utc),
        finished_at=row.get("finished_at"),
    )


async def get_context(parliament_id: str) -> ParliamentRuntimeContext | None:
    if _skip_db():
        async with _LOCK:
            return _CONTEXTS.get(parliament_id)
    row = await asyncio.to_thread(_db_fetch, parliament_id)
    if row is None:
        return None
    return ParliamentRuntimeContext(
        parliament_id=parliament_id,
        user_interrupt=row.get("user_interrupt"),
        cancel_requested=row.get("cancel_requested", False),
        is_running=row.get("is_running", False),
        started_at=row.get("started_at") or datetime.now(timezone.utc),
        finished_at=row.get("finished_at"),
    )


async def set_user_interrupt(parliament_id: str, content: str) -> bool:
    """写入用户插话。返回 True 表示成功（议会还在跑）。"""
    if _skip_db():
        async with _LOCK:
            ctx = _CONTEXTS.get(parliament_id)
            if ctx is None or ctx.finished_at is not None:
                return False
            ctx.user_interrupt = content
            return True
    # DB：先检查 is_running + 没 finished
    row = await asyncio.to_thread(_db_fetch, parliament_id)
    if row is None or row.get("finished_at") is not None:
        return False
    await _db_upsert(parliament_id, user_interrupt=content)
    return True


async def consume_user_interrupt(parliament_id: str) -> str | None:
    """读出并清空用户插话（一次性）。"""
    if _skip_db():
        async with _LOCK:
            ctx = _CONTEXTS.get(parliament_id)
            if ctx is None:
                return None
            msg = ctx.user_interrupt
            ctx.user_interrupt = None
            return msg
    row = await asyncio.to_thread(_db_fetch, parliament_id)
    if row is None:
        return None
    msg = row.get("user_interrupt")
    if msg:
        await _db_upsert(parliament_id, user_interrupt=None)
    return msg


async def request_cancel(parliament_id: str) -> bool:
    """请求取消。返回 True 表示议会还在跑（请求有效）。"""
    if _skip_db():
        async with _LOCK:
            ctx = _CONTEXTS.get(parliament_id)
            if ctx is None or ctx.finished_at is not None:
                return False
            ctx.cancel_requested = True
            return True
    row = await asyncio.to_thread(_db_fetch, parliament_id)
    if row is None or row.get("finished_at") is not None:
        return False
    await _db_upsert(parliament_id, cancel_requested=True)
    return True


async def is_cancelled(parliament_id: str) -> bool:
    """检查取消状态。"""
    if _skip_db():
        async with _LOCK:
            ctx = _CONTEXTS.get(parliament_id)
            return ctx is not None and ctx.cancel_requested
    row = await asyncio.to_thread(_db_fetch, parliament_id)
    return row is not None and row.get("cancel_requested", False)


async def mark_running(parliament_id: str) -> None:
    if _skip_db():
        async with _LOCK:
            ctx = _CONTEXTS.get(parliament_id)
            if ctx is not None:
                ctx.is_running = True
                ctx.finished_at = None
                ctx.cancel_requested = False
        return
    now = datetime.now(timezone.utc)
    await _db_upsert(
        parliament_id,
        is_running=True,
        finished_at=None,
        cancel_requested=False,
        started_at=now,
    )


async def mark_finished(parliament_id: str) -> None:
    if _skip_db():
        async with _LOCK:
            ctx = _CONTEXTS.get(parliament_id)
            if ctx is not None:
                ctx.is_running = False
                ctx.finished_at = datetime.now(timezone.utc)
        return
    now = datetime.now(timezone.utc)
    await _db_upsert(parliament_id, is_running=False, finished_at=now)


# ============ 测试用辅助 ============

def _clear_all_for_tests() -> None:
    """仅供测试调用：清空所有上下文。"""
    _CONTEXTS.clear()
