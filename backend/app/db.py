"""Supabase 客户端单例（用 supabase-py）。

主要用于 HTTP API（创建议会、读取议会状态等）。
数据 seed / migration 用 app.db_psycopg 直连 Postgres。
"""
import re
from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings


@lru_cache
def get_supabase() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_key)


def direct_db_url() -> str:
    """生产 worker / web 都走这条 DB URL，绕过 Supabase Supavisor pooler。

    pooler（6543, transaction mode）下裸 psycopg.connect 会复用连接，第二次
    execute 触发 prepared statement 报错「prepared statement S_1 already exists」。

    优先级：
      1. SUPABASE_DB_URL_DIRECT（直连 5432，云端推荐）
      2. SUPABASE_DB_URL（pooler，本地 fallback；务必配好 prepare_threshold=None）

    本函数 strip 掉 query string，调用方拿到干净的 conninfo。
    """
    s = get_settings()
    direct = (s.supabase_db_url_direct or "").strip()
    url = direct if direct else s.supabase_db_url
    return re.sub(r"\?.*$", "", url)
