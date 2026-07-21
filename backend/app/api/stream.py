"""SSE 流式端点：只读 parliament_events 表。P0-S2.3 重写为纯订阅器。

之前 SSE 兼任务执行（V2）→ 现在 SSE 只读 events 表。
500ms 轮询，拿到新事件就推给前端。
"""
import asyncio
import json
import logging
import time

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.db import get_supabase
from app.event_store import fetch_events_since
from app.middleware.rate_limit import _get_device_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/parliament", tags=["stream"])

# 轮询间隔
POLL_INTERVAL_SECONDS = 0.5
# 最大观看时长（15 分钟后强制断开）
MAX_WATCH_SECONDS = 15 * 60


async def _load_parliament_from_db(parliament_id: str) -> dict | None:
    """从数据库读取议会基本信息。"""
    supabase = get_supabase()
    result = (
        supabase.table("parliaments")
        .select("*")
        .eq("id", parliament_id)
        .single()
        .execute()
    )
    return result.data if result.data else None


@router.get("/{parliament_id}/stream")
async def stream_parliament(parliament_id: str, request: Request):
    """订阅议会进度：500ms 轮询 events 表。

    P0-S2.3: 这个 endpoint 不再跑 LangGraph。
    真正的执行在 worker.py。
    """

    async def event_generator():
        # 1. 校验议会存在 + 所有权
        row = await _load_parliament_from_db(parliament_id)
        if row is None:
            yield {
                "event": "message",
                "data": json.dumps(
                    {"type": "error", "error": "议会不存在"},
                    ensure_ascii=False,
                ),
            }
            return

        stored_device = row.get("device_id")
        # P0-S2.3: 所有权校验（生产环境）
        # 开发环境跳过——因为 EventSource API 不支持自定义 header，
        # 加上 Next.js rewrite 不转发 X-Device-Id，导致开发时容易误拦
        settings = get_settings()
        if stored_device and settings.app_env != "development":
            request_device = _get_device_id(request)
            if request_device != stored_device:
                yield {
                    "event": "message",
                    "data": json.dumps(
                        {"type": "error", "error": "无权访问此议会"},
                        ensure_ascii=False,
                    ),
                }
                return

        # 2. 推送议会 started 事件（给前端展示主题）
        topic = row.get("topic", "")
        category = row.get("topic_category")
        yield {
            "event": "message",
            "data": json.dumps(
                {
                    "type": "parliament_started",
                    "parliament_id": parliament_id,
                    "topic": topic,
                    "payload": {"topic": topic, "category": category},
                },
                ensure_ascii=False,
            ),
        }

        # 3. 轮询 events
        last_seq = 0
        start_time = time.time()
        terminal_types = {"parliament_done", "cancelled", "error"}

        while True:
            # 客户端断开
            if await request.is_disconnected():
                return

            # 超时
            if time.time() - start_time > MAX_WATCH_SECONDS:
                yield {
                    "event": "message",
                    "data": json.dumps(
                        {"type": "error", "error": "观看超时"},
                        ensure_ascii=False,
                    ),
                }
                return

            # 抓新事件
            try:
                events = await asyncio.to_thread(
                    fetch_events_since, parliament_id, last_seq
                )
            except Exception as e:
                logger.warning("fetch_events_since 失败: %s", e)
                events = []

            for e in events:
                event_type = e["event_type"]
                payload = e.get("payload") or {}
                # 终端事件类型（SSE 兼容 stream 里以前用的 type 字段）
                sse_data = dict(payload)
                sse_data["type"] = event_type
                sse_data["parliament_id"] = parliament_id
                sse_data["seq"] = e["seq"]

                yield {
                    "event": "message",
                    "data": json.dumps(
                        sse_data,
                        ensure_ascii=False,
                        default=str,
                    ),
                }
                last_seq = e["seq"]

                if event_type in terminal_types:
                    return

            # 心跳 ping（每 15s 一次）
            if int(time.time()) % 15 == 0:
                yield {"event": "ping", "data": ""}

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    return EventSourceResponse(event_generator())
