"""Supabase 客户端单例（用 supabase-py）。

主要用于 HTTP API（创建议会、读取议会状态等）。
数据 seed / migration 用 app.db_psycopg 直连 Postgres。
"""
from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings


@lru_cache
def get_supabase() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_key)
