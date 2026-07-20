"""SSE 流式端点：订阅议会进度。"""
import asyncio
import json
import logging
import time

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.db import get_supabase
from app.graph.parliament import get_graph_with_checkpointer
from app.graph.state import make_initial_state
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

        # 5. 流式跑状态机（V2-S1: checkpointer + thread_id 续盘）
        last_heartbeat = time.time()
        try:
            graph = await get_graph_with_checkpointer()
            config = {"configurable": {"thread_id": parliament_id}}
            # V2-S1: 如果该 thread 已有 checkpoint，传 None 让 LangGraph 自动续；
            # 否则传 initial_state 跑全图
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
                # V1-S4 预留：取消请求检查
                if await is_cancelled(parliament_id):
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
            yield {
                "event": "message",
                "data": json.dumps(
                    {"type": "parliament_done", "parliament_id": parliament_id},
                    ensure_ascii=False,
                ),
            }

        except asyncio.CancelledError:
            # P0-S1: 客户端断开 / cancel 触发
            logger.info("议会 %s 被 cancel 或客户端断开", parliament_id)
            await _mark_parliament_failed(parliament_id, "cancelled_or_disconnected")
            raise
        except Exception as e:
            logger.exception("议会 %s 异常: %s", parliament_id, e)
            # P0-S1: 异常时真把 parliaments.status 改成 failed
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
