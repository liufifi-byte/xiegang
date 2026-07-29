"""LangGraph Postgres checkpointer 单例。

V2-S1：让状态机能跨重启 / 多 worker 共享 checkpoint。

每个议会 = 一个 thread_id（直接用 parliament_id）
节点完成时自动把 state 写入 Postgres。

注意：Supabase pooler (PgBouncer transaction mode) 不支持 prepared statements。
生产部署配 SUPABASE_DB_URL_DIRECT 走 5432 直连，绕开该问题。
"""
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config import get_settings
from app.db import direct_db_url


_db_url_cache: str | None = None


def _clean_db_url() -> str:
    """优先 SUPABASE_DB_URL_DIRECT（5432 直连），fallback 到 pooler。"""
    global _db_url_cache
    if _db_url_cache is None:
        _db_url_cache = direct_db_url()
    return _db_url_cache


_saver: AsyncPostgresSaver | None = None
_setup_done: bool = False


async def get_checkpointer() -> AsyncPostgresSaver:
    """返回 AsyncPostgresSaver 单例。

    生产直连 5432 时可调 setup() 自动建表；
    fallback 到 pooler 时跳过 setup()（pooler 不支持 CREATE INDEX CONCURRENTLY）——
    手动跑 scripts/migrate_009_langgraph_tables.py。
    """
    global _saver, _setup_done

    if _saver is None:
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            conninfo=_clean_db_url(),
            kwargs={"prepare_threshold": None},
            min_size=0,
            max_size=10,
            timeout=10,
            max_lifetime=300,
            max_idle=60,
            reconnect_timeout=30,
            check=AsyncConnectionPool.check_connection,
            open=False,
        )
        await pool.open()
        _saver = AsyncPostgresSaver(conn=pool)

    # 仅在走直连（5432）时允许 setup()，pooler 则手动建表。
    s = get_settings()
    is_direct = bool((s.supabase_db_url_direct or "").strip())
    if is_direct and not _setup_done:
        await _saver.setup()
        _setup_done = True
    else:
        # pooler 路径：手动跑 migrations/009_langgraph_tables.py 建表。
        _setup_done = True

    return _saver
