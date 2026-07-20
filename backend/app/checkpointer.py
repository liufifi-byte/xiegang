"""LangGraph Postgres checkpointer 单例。

V2-S1：让状态机能跨重启 / 多 worker 共享 checkpoint。

每个议会 = 一个 thread_id（直接用 parliament_id）
节点完成时自动把 state 写入 Postgres。

注意：Supabase pooler (PgBouncer transaction mode) 不支持 prepared statements。
"""
import re

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config import get_settings


_db_url_cache: str | None = None


def _clean_db_url() -> str:
    """直接用 SUPABASE_DB_URL_DIRECT（langgraph checkpointer 专用直连）。

    P0-S2: 走 pooler 会跟 prepared statements + CREATE INDEX CONCURRENTLY 冲突。
    直连 5432 端口绕开 PgBouncer。fallback 到 pooler URL。
    """
    global _db_url_cache
    if _db_url_cache is None:
        url = get_settings().supabase_db_url_direct or get_settings().supabase_db_url
        _db_url_cache = re.sub(r"\?.*$", "", url)
    return _db_url_cache


_saver: AsyncPostgresSaver | None = None
_setup_done: bool = False


async def get_checkpointer() -> AsyncPostgresSaver:
    """返回 AsyncPostgresSaver 单例。

    P0-S2 fix: Supabase PgBouncer 不支持 prepared statements，
    所以手动构造 AsyncConnectionPool 传 prepare_threshold=None，
    用 AsyncPostgresSaver(pool=...) 创建。
    """
    global _saver, _setup_done

    if _saver is None:
        from psycopg_pool import AsyncConnectionPool

        # AsyncConnectionPool 接受 psycopg connect_kwargs
        pool = AsyncConnectionPool(
            conninfo=_clean_db_url(),
            kwargs={"prepare_threshold": None},
            open=False,
        )
        await pool.open()
        _saver = AsyncPostgresSaver(conn=pool)

    if not _setup_done:
        await _saver.setup()
        _setup_done = True

    return _saver
