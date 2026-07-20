"""议会 HTTP API。"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import get_settings
from app.db import get_supabase
from app.middleware.rate_limit import (
    _check_and_record,
    _get_client_ip,
    _get_device_id,
)
from app.runtime_context import request_cancel, set_user_interrupt
from app.schemas.parliament import (
    CreateParliamentRequest,
    ParliamentResponse,
)

router = APIRouter(prefix="/api/parliament", tags=["parliament"])


class InterruptRequest(BaseModel):
    """用户插话请求体。"""
    content: str


@router.post("")
async def create_parliament(req: CreateParliamentRequest, request: Request) -> dict:
    """创建议会，返回议会 ID 和 SSE 流 URL。

    P0-S1.1: 加了 IP + device_id 双轨限流。
    """
    settings = get_settings()
    ip = _get_client_ip(request)
    device_id = _get_device_id(request)

    # P0-S1.1: 限流检查
    allowed, reason = await _check_and_record(
        ip=ip,
        device_id=device_id,
        ip_per_minute=settings.rate_limit_ip_per_minute,
        device_daily=settings.rate_limit_device_daily,
    )
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": reason, "retry_after_seconds": 60},
        )

    supabase = get_supabase()

    row = {
        "topic": req.topic,
        "topic_category": req.topic_category,
        "user_background": req.user_background,
        "user_id": req.user_id,
        "device_id": device_id,  # P0-S2: 所有权校验用
        "status": "queued",     # P0-S2: Worker 抢任务用
        "members_state": {},
        "transcript": [],
        "tool_calls": [],
    }
    result = supabase.table("parliaments").insert(row).execute()

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create parliament")

    parliament_id = result.data[0]["id"]
    return {
        "parliament_id": parliament_id,
        "stream_url": f"/api/parliament/{parliament_id}/stream",
        "device_id": device_id,  # P0-S1.1: 回传让前端记
    }


@router.post("/{parliament_id}/interrupt")
async def interrupt_parliament(parliament_id: str, req: InterruptRequest) -> dict:
    """V1-S3: 用户在群里插话 → 写入运行时上下文。

    议会下一轮 debate 节点检查 user_interrupt，注入 prompt。
    """
    if not req.content or not req.content.strip():
        raise HTTPException(status_code=400, detail="内容不能为空")

    ok = await set_user_interrupt(parliament_id, req.content.strip())
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="议会不存在或已结束，无法插话",
        )

    return {
        "ok": True,
        "parliament_id": parliament_id,
        "queued_at": True,
    }


@router.post("/{parliament_id}/cancel")
async def cancel_parliament(parliament_id: str) -> dict:
    """V1-S4 / V2-S2: 请求取消议会。

    设置 cancel_requested 标记，SSE 流每次 yield 检查并提前 break。
    V2-S2: 同时遍历该议会所有 in-flight LLM 调用，task.cancel() 硬中断。
    """
    from app.cancel_guard import cancel_all_inflight

    ok = await request_cancel(parliament_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="议会不存在或已结束",
        )
    # V2-S2: 硬中断所有正在飞的 LLM 调用
    cancelled_n = await cancel_all_inflight(parliament_id)
    return {
        "ok": True,
        "parliament_id": parliament_id,
        "message": f"取消请求已收到，已中断 {cancelled_n} 个进行中的 LLM 调用",
        "cancelled_tasks": cancelled_n,
    }


@router.get("/{parliament_id}")
async def get_parliament(parliament_id: str) -> ParliamentResponse:
    """获取议会状态（含 transcript / summary 等）。"""
    supabase = get_supabase()
    result = (
        supabase.table("parliaments")
        .select("*")
        .eq("id", parliament_id)
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="Parliament not found")

    data = result.data
    return ParliamentResponse(
        id=data["id"],
        topic=data["topic"],
        status=data["status"],
        created_at=data["created_at"],
        finished_at=data.get("finished_at"),
        summary=data.get("summary"),
        transcript=data.get("transcript", []),
        tool_calls=data.get("tool_calls", []),
    )


@router.get("/")
async def list_parliaments(limit: int = 20) -> dict:
    """列出最近的议会（前端列表用）。"""
    supabase = get_supabase()
    result = (
        supabase.table("parliaments")
        .select("id, topic, status, created_at, topic_category")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return {"items": result.data or []}
