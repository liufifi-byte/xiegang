"""Supabase 客户端单例（用 supabase-py）。

主要用于 HTTP API（创建议会、读取议会状态等）。
数据 seed / migration 用 app.db_psycopg 直连 Postgres。
"""
import re
import socket
from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings


@lru_cache
def get_supabase() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_key)


def _host_supports_ipv6(host: str) -> bool:
    """探测目标 host 是否能解析到 IPv6 且本机能 reach IPv6 网络。

    关键用途：腾讯云轻量 / 部分 VPS 默认无 IPv6 出站，但 Supabase 直连
    (5432 端口) 域名只有 AAAA 记录。如果走 IPv6 直连会持续报
    `Network is unreachable`，需要 fallback 到 pooler URL（其域名有 IPv4）。
    """
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET6, socket.SOCK_STREAM)
        return bool(infos)
    except (socket.gaierror, OSError):
        return False


def _local_has_ipv6_route() -> bool:
    """本机是否有可用的 IPv6 出站路径。

    通过尝试创建 IPv6 socket 检测：腾讯云轻量服务器会返回 False，
    一些无 IPv6 的本机开发环境也会返回 False。
    """
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            # 不真连，只判断本机有没有 IPv6 stack 配置
            s.bind(("::1", 0))
            # 进一步：试连 Google DNS IPv6（公认稳） confirm 出站路径
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
                probe.settimeout(1.0)
                try:
                    probe.connect(("2001:4860:4860::8888", 53))
                    return True
                except OSError:
                    return False
    except OSError:
        return False


def direct_db_url() -> str:
    """生产 worker / web 都走这条 DB URL，绕过 Supabase Supavisor pooler。

    pooler（6543, transaction mode）下裸 psycopg.connect 会复用连接，第二次
    execute 触发 prepared statement 报错「prepared statement S_1 already exists」。

    优先级（带 IPv6 reachability 检测，治本 IPv6 问题）：
      1. SUPABASE_DB_URL_DIRECT（直连 5432，云端推荐）
         但**仅当** host 解析到 IPv6 + 本机能 reach IPv6 时才用
         （避免腾讯云轻量无 IPv6 出站导致 worker 整个跑不起来）
      2. SUPABASE_DB_URL（pooler，IPv4 可达、绕开 IPv6 问题）

    本函数 strip 掉 query string，调用方拿到干净的 conninfo。
    """
    s = get_settings()
    direct = (s.supabase_db_url_direct or "").strip()
    pooler = s.supabase_db_url

    if not direct:
        # 没配 direct URL，强制走 pooler fallback
        return re.sub(r"\?.*$", "", pooler)

    # 探测 direct URL 的 host 是否只走 IPv6 且本机无 IPv6 路由
    host_match = re.search(r"@([^@:/?]+)", direct)
    if host_match:
        host = host_match.group(1)
        host_is_ipv6 = _host_supports_ipv6(host)
        host_has_a = False
        try:
            socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
            host_has_a = True
        except OSError:
            host_has_a = False

        # 双栈 OK → 用 direct；只有 IPv6 但本机没 IPv6 → fallback pooler
        if host_is_ipv6 and not host_has_a and not _local_has_ipv6_route():
            return re.sub(r"\?.*$", "", pooler)

    return re.sub(r"\?.*$", "", direct)
