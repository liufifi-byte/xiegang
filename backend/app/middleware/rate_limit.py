"""P0-S1.1 限流中间件：IP + anonymous device_id 双轨。

规则：
- 单 IP 每分钟创建议会 ≤ RATE_LIMIT_IP_PER_MINUTE（默认 2）
- 单 device_id 每天最多 RATE_LIMIT_DEVICE_DAILY（默认 10）
- 同一 device_id 并发活跃议会 ≤ 1（queued/running/awaiting_input 状态算活跃）

并发检查从 in-memory set 改为查 DB（Codex fix）：
  原来的 _RUNNING_DEVICES 只在 mark_device_finished 时清，
  但 awaiting_input 状态从来没调过 mark_device_finished → 永远卡住。
  web 进程和 worker 进程内存不共享，in-memory set 天然不可靠。
  → 改为查 parliaments 表的实时状态，准确且跨进程安全。
"""
import asyncio
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse


# in-memory 状态（仅用于 IP 和 device 的时间窗限流，不涉及并发状态）
_IP_REQUESTS: dict[str, deque[float]] = defaultdict(deque)
_DEVICE_DAILY: dict[str, list[float]] = defaultdict(list)
_ACTIVE_DEVICE_CACHE: dict[str, float] = {}  # device_id → 最后一次查 DB 的时间戳（5s TTL 减少 DB 压力）
_LOCK = asyncio.Lock()

_ACTIVE_STATUSES = ("queued", "running", "awaiting_input")
_CACHE_TTL_SECONDS = 5  # 并发检查 5s 缓存


def _get_device_id(request: Request) -> str:
    """从 X-Device-Id header 拿；没 header 用 IP 作 fallback。"""
    return request.headers.get("X-Device-Id") or f"ip:{_get_client_ip(request)}"


def _get_client_ip(request: Request) -> str:
    """拿客户端 IP，处理反代情况。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _has_active_parliament(device_id: str) -> bool:
    """查 DB 看该 device 是否有活跃议会（queued/running/awaiting_input）。

    带 5s 缓存减少 DB 查询量（短时间内多次 POST 不会每次都打 DB）。
    """
    now = time.time()
    cached_time = _ACTIVE_DEVICE_CACHE.get(device_id)
    if cached_time and now - cached_time < _CACHE_TTL_SECONDS:
        # 缓存认为活跃 = 直接返回 True（保守策略）
        return True

    from app.db import get_supabase

    try:
        result = (
            get_supabase()
            .table("parliaments")
            .select("id")
            .eq("device_id", device_id)
            .in_("status", list(_ACTIVE_STATUSES))
            .limit(1)
            .execute()
        )
        has_active = bool(result.data)
        if has_active:
            _ACTIVE_DEVICE_CACHE[device_id] = now
        else:
            _ACTIVE_DEVICE_CACHE.pop(device_id, None)
        return has_active
    except Exception:
        # DB 查不了 → 放行（宁可多放一条不要误杀正常用户）
        return False


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
        while ip_window and ip_window[0] < now - 60:
            ip_window.popleft()
        if len(ip_window) >= ip_per_minute:
            return False, f"该 IP 每分钟创建议会数已达上限 ({ip_per_minute})"

        # 2. device 每天上限（24 小时）
        device_history = _DEVICE_DAILY[device_id]
        cutoff_24h = now - 86400
        device_history[:] = [t for t in device_history if t > cutoff_24h]
        if len(device_history) >= device_daily:
            return False, f"该设备今日议会数已达上限 ({device_daily} 场/天)"

        # 3. 同 device 并发活跃议会 ≤ 1 → 查 DB（替代 in-memory set）
        if await _has_active_parliament(device_id):
            return False, "你已有一场活跃会议，请完成或取消后再开新的"

        # 通过！记录
        ip_window.append(now)
        device_history.append(now)
        return True, ""


async def mark_device_finished(device_id: str) -> None:
    """议会结束时清除缓存（下次查 DB 会自然得到正确结果）。"""
    _ACTIVE_DEVICE_CACHE.pop(device_id, None)


def _clear_all_for_tests() -> None:
    _IP_REQUESTS.clear()
    _DEVICE_DAILY.clear()
    _ACTIVE_DEVICE_CACHE.clear()
