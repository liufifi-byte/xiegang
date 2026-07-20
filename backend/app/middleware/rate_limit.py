"""P0-S1.1 限流中间件：IP + anonymous device_id 双轨。

规则：
- 单 IP 每分钟创建议会 ≤ RATE_LIMIT_IP_PER_MINUTE（默认 2）
- 单 device_id 每天最多 RATE_LIMIT_DEVICE_DAILY（默认 10）
- 同一 device_id 并发议会 ≤ 1（同一时刻只能跑一场）→ 409

实现：in-memory dict + lock，进程级。
MVP 阶段够用；多 worker 时考虑换 Redis。

限流只针对 POST /api/parliament（创建议会）。
其他 GET 请求不限。
"""
import asyncio
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse


# in-memory 状态
_IP_REQUESTS: dict[str, deque[float]] = defaultdict(deque)
_DEVICE_DAILY: dict[str, list[float]] = defaultdict(list)  # device_id → 时间戳列表
_RUNNING_DEVICES: set[str] = set()  # 当前在跑议会的 device_id
_LOCK = asyncio.Lock()


def _get_device_id(request: Request) -> str:
    """从 X-Device-Id header 拿；没 header 用 IP 作 fallback。"""
    return request.headers.get("X-Device-Id") or f"ip:{_get_client_ip(request)}"


def _get_client_ip(request: Request) -> str:
    """拿客户端 IP，处理反代情况。"""
    # Railway/Vercel/Nginx 反代会塞 X-Forwarded-For
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _check_and_record(
    ip: str,
    device_id: str,
    ip_per_minute: int,
    device_daily: int,
) -> tuple[bool, str]:
    """检查是否允许创建议会。返回 (allowed, reason)。"""
    async with _LOCK:
        now = time.time()

        # 1. IP 每分钟上限
        ip_window = _IP_REQUESTS[ip]
        # 清 60s 前的
        while ip_window and ip_window[0] < now - 60:
            ip_window.popleft()
        if len(ip_window) >= ip_per_minute:
            return False, f"该 IP 每分钟创建议会数已达上限 ({ip_per_minute})"
        # 先不记，下面 3 通过再记

        # 2. device 每天上限（24 小时）
        device_history = _DEVICE_DAILY[device_id]
        # 清 24h 前的
        cutoff_24h = now - 86400
        device_history[:] = [t for t in device_history if t > cutoff_24h]
        if len(device_history) >= device_daily:
            return False, f"该设备今日议会数已达上限 ({device_daily} 场/天)"
        # 先不记

        # 3. 同 device 并发议会 ≤ 1
        if device_id in _RUNNING_DEVICES:
            return False, "已有议会正在运行，请等其结束后再开新议会"

        # 通过！记录
        ip_window.append(now)
        device_history.append(now)
        _RUNNING_DEVICES.add(device_id)
        return True, ""


async def mark_device_finished(device_id: str) -> None:
    """议会结束时调用，从 _RUNNING_DEVICES 移除。"""
    async with _LOCK:
        _RUNNING_DEVICES.discard(device_id)


def _clear_all_for_tests() -> None:
    _IP_REQUESTS.clear()
    _DEVICE_DAILY.clear()
    _RUNNING_DEVICES.clear()
