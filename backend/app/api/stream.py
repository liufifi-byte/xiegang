"""SSE 流式端点：订阅议会进度。"""
import asyncio
import json
import logging
import time

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.db import get_supabase
from app.graph.parliament import get_graph_with_checkpointer
from app.graph.state import make_initial_state
from app.monitoring import log_parliament_failed, log_parliament_started, log_parliament_succeeded
from app.runtime_context import (
    get_or_create_context,
    is_cancelled,
    mark_finished,
    mark_running,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/parliament", tags=["stream"])


# SSE 心跳间隔，避免代理掐断长连接
HEARTBEAT_SECONDS = 15


async def _mark_parliament_failed(parliament_id: str, reason: str) -> None:
    """P0-S1: 节点炸时把 parliaments.status 真标为 failed，不卡 running。"""
    try:
        supabase = get_supabase()
        supabase.table("parliaments").update({
            "status": "failed",
            "finished_at": "now()",
            "error_message": reason[:500],
        }).eq("id", parliament_id).execute()
    except Exception as db_err:
        logger.error(
            "无法把议会标记为 failed: parliament_id=%s db_err=%s",
            parliament_id, db_err,
        )


async def _load_parliament_from_db(parliament_id: str) -> dict | None:
    """从数据库读取议会原始 topic + 用户背景。"""
    supabase = get_supabase()
    result = (
        supabase.table("parliaments")
        .select("*")
        .eq("id", parliament_id)
        .single()
        .execute()
    )
    return result.data if result.data else None


def _build_state_from_db_row(row: dict, parliament_id: str) -> dict:
    """把数据库行转回 LangGraph state。"""
    return make_initial_state(
        topic=row["topic"],
        user_id=row.get("user_id"),
        topic_category=row.get("topic_category"),
        user_background=row.get("user_background"),
        parliament_id=parliament_id,
    ) | {"started_at": time.time()}


@router.get("/{parliament_id}/stream")
async def stream_parliament(parliament_id: str, request: Request):
    """订阅议会进度：SSE 流式推送事件。"""

    async def event_generator():
        # 1. 从数据库读议会基础信息
        row = await _load_parliament_from_db(parliament_id)
        if row is None:
            yield {
                "event": "message",
                "data": json.dumps(
                    {"type": "error", "error": "议会不存在", "parliament_id": parliament_id},
                    ensure_ascii=False,
                ),
            }
            return

        # 2. 标记数据库状态为 running + 注册运行时上下文（V1-S3）
        try:
            supabase = get_supabase()
            supabase.table("parliaments").update(
                {"status": "running"}
            ).eq("id", parliament_id).execute()
        except Exception:
            pass
        ctx = await get_or_create_context(parliament_id)
        await mark_running(parliament_id)

        # 3. 构造初始 state
        initial_state = _build_state_from_db_row(row, parliament_id)

        # P0-S1.4: 监控起点
        parliament_start_ts = time.time()
        log_parliament_started(parliament_id, row.get("user_id"), row.get("topic", ""))

        # 4. 推送启动事件
        yield {
            "event": "message",
            "data": json.dumps(
                {
                    "type": "parliament_started",
                    "parliament_id": parliament_id,
                    "topic": row["topic"],
                    "payload": {
                        "topic": row["topic"],
                        "category": row.get("topic_category"),
                    },
                },
                ensure_ascii=False,
            ),
        }

        if await request.is_disconnected():
            await mark_finished(parliament_id)
            return

        # P0-S1.2: 整体议会超时 watchdog
        settings = get_settings()
        total_timeout = settings.parliament_total_timeout
        timed_out = False

        # 5. 流式跑状态机（V2-S1: checkpointer + thread_id 续盘）
        last_heartbeat = time.time()
        try:
            graph = await get_graph_with_checkpointer()
            config = {"configurable": {"thread_id": parliament_id}}
            from langgraph.types import StateSnapshot
            try:
                snapshot: StateSnapshot = await graph.aget_state(config)
                has_checkpoint = snapshot is not None and snapshot.next
            except Exception:
                has_checkpoint = False
            run_input = None if has_checkpoint else initial_state

            async for event in graph.astream(
                run_input,
                config=config,
            ):
                # P0-S1.2: 整体超时检查
                elapsed = time.time() - parliament_start_ts
                if elapsed > total_timeout:
                    logger.warning(
                        "议会 %s 超时（%ds > %ds），强制终止",
                        parliament_id, int(elapsed), total_timeout,
                    )
                    timed_out = True
                    from app.cancel_guard import cancel_all_inflight
                    await cancel_all_inflight(parliament_id)
                    break

                # V1-S4 + P0-S1.3: 取消请求检查
                if await is_cancelled(parliament_id):
                    log_parliament_cancelled(parliament_id)
                    await _mark_parliament_failed(parliament_id, "user_cancelled")
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {"type": "cancelled", "parliament_id": parliament_id},
                            ensure_ascii=False,
                        ),
                    }
                    break

                # LangGraph astream 输出：{node_name: state_update}
                for node_name, state_update in event.items():
                    safe_payload = _safe_serialize(state_update)
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {
                                "type": "node_complete",
                                "node": node_name,
                                "parliament_id": parliament_id,
                                "payload": safe_payload,
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    }

                if await request.is_disconnected():
                    break

                # 心跳
                now = time.time()
                if now - last_heartbeat > HEARTBEAT_SECONDS:
                    last_heartbeat = now
                    yield {"event": "ping", "data": f" - {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}"}

            # 6. 跑完
            if timed_out:
                duration = time.time() - parliament_start_ts
                log_parliament_failed(parliament_id, "total_timeout", duration)
                await _mark_parliament_failed(parliament_id, f"议会总时长超过 {total_timeout}s，已强制终止")
                yield {
                    "event": "message",
                    "data": json.dumps(
                        {
                            "type": "error",
                            "parliament_id": parliament_id,
                            "error": f"议会超时（{total_timeout}s），已强制终止",
                        },
                        ensure_ascii=False,
                    ),
                }
            else:
                duration = time.time() - parliament_start_ts
                log_parliament_succeeded(parliament_id, duration)
                yield {
                    "event": "message",
                    "data": json.dumps(
                        {"type": "parliament_done", "parliament_id": parliament_id},
                        ensure_ascii=False,
                    ),
                }

        except asyncio.CancelledError:
            duration = time.time() - parliament_start_ts
            log_parliament_failed(parliament_id, "cancelled_or_disconnected", duration)
            # P0-S1.2: 客户端断开 / cancel 触发，硬中断所有在飞 LLM
            from app.cancel_guard import cancel_all_inflight
            await cancel_all_inflight(parliament_id)
            await _mark_parliament_failed(parliament_id, "cancelled_or_disconnected")
            raise
        except Exception as e:
            duration = time.time() - parliament_start_ts
            log_parliament_failed(parliament_id, str(e), duration)
            logger.exception("议会 %s 异常: %s", parliament_id, e)
            # P0-S1.0: 异常时真把 parliaments.status 改成 failed
            # P0-S1.2: 同时硬中断所有在飞 LLM 调用
            from app.cancel_guard import cancel_all_inflight
            await cancel_all_inflight(parliament_id)
            await _mark_parliament_failed(parliament_id, str(e))
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "error",
                        "parliament_id": parliament_id,
                        "error": f"parliament stream error: {e}",
                    },
                    ensure_ascii=False,
                ),
            }
        finally:
            await mark_finished(parliament_id)
            # P0-S1.1: 释放该议会的并发占位 + device_id 槽
            # 从 DB 查 device_id（POST 时由前端 X-Device-Id 传，但这里查不到；用 row 的 user_id 兜底）
            from app.middleware.rate_limit import mark_device_finished
            device_id = (row.get("user_id") or "_unknown_device") if row else "_unknown_device"
            await mark_device_finished(device_id)

    return EventSourceResponse(event_generator())


def _safe_serialize(obj: dict) -> dict:
    """过滤掉 state_update 里无法 JSON 序列化的字段。"""
    if not isinstance(obj, dict):
        return {}
    out = {}
    for k, v in obj.items():
        try:
            json.dumps(v, default=str)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = str(v)
    return out
