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
    """用 pooler URL（SUPABASE_DB_URL）。

    P0-S2 fix: 直连 5432 端口从本地 IPv6 网络不通（Connection refused）。
    直接用 pooler 6543 + prepare_threshold=None + 手动建表脚本。
    """
    global _db_url_cache
    if _db_url_cache is None:
        url = get_settings().supabase_db_url
        _db_url_cache = re.sub(r"\?.*$", "", url)
    return _db_url_cache


_saver: AsyncPostgresSaver | None = None
_setup_done: bool = False


async def get_checkpointer() -> AsyncPostgresSaver:
    """返回 AsyncPostgresSaver 单例。

    P0-S2 fix: Supabase 直连 5432 从本地网络不通 + pooler 6543 不支持
    CREATE INDEX CONCURRENTLY。 workaround：
    1. 用 pooler URL 创建 pool
    2. 手动跳过 setup()（手动跑 migration 建 langgraph 的表）
       脚本: scripts/migrate_009_langgraph_tables.py
    """
    global _saver, _setup_done

    if _saver is None:
        from psycopg_pool import AsyncConnectionPool

        # 用 pooler URL（直连不通）
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

    # 不调 setup()——pooler 不支持 CONCURRENTLY。
    # 手动跑 migrations/009_langgraph_tables.py 建表。
    _setup_done = True

    return _saver
