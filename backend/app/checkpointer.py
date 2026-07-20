"""LangGraph Postgres checkpointer 单例。

V2-S1：让状态机能跨重启 / 多 worker 共享 checkpoint。

每个议会 = 一个 thread_id（直接用 parliament_id）
节点完成时自动把 state 写入 Postgres。
"""
import re
from functools import lru_cache

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config import get_settings


@lru_cache
def _clean_db_url() -> str:
    """psycopg 不喜欢某些 pgbouncer query 参数，去掉。"""
    url = get_settings().supabase_db_url
    return re.sub(r"\?.*$", "", url)


_saver: AsyncPostgresSaver | None = None
_setup_done: bool = False


async def get_checkpointer() -> AsyncPostgresSaver:
    """返回 AsyncPostgresSaver 单例（首次调用时 setup 建表）。"""
    global _saver, _setup_done
    if _saver is None:
        _saver = AsyncPostgresSaver.from_conn_string(_clean_db_url())
    if not _setup_done:
        await _saver.setup()
        _setup_done = True
    return _saver
