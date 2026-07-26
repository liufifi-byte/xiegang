"""议会 HTTP API。"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import get_settings
from app.db import get_supabase
from app.event_store import fetch_events_since
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


class HeartbeatRequest(BaseModel):
    """The page is still attached to this council."""

    keep_running: bool = False


def _worker_available() -> bool:
    """A worker is available when it has checked in during the last 15 seconds."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=15)).isoformat()
        result = (
            get_supabase()
            .table("worker_heartbeats")
            .select("worker_id")
            .gte("last_seen_at", cutoff)
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception:
        return False


@router.post("")
async def create_parliament(req: CreateParliamentRequest, request: Request) -> dict:
    """创建议会，返回议会 ID 和 SSE 流 URL。

    P0-S1.1: 加了 IP + device_id 双轨限流。
    P0-S2 fix: 开发环境（APP_ENV=development）跳过限流方便测试。
    """
    settings = get_settings()
    ip = _get_client_ip(request)
    device_id = _get_device_id(request)

    # P0-S1.1: 限流检查（开发环境跳过）
    if settings.app_env != "development":
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
        "selected_expert_ids": req.selected_expert_ids,
        "current_phase": "queued",
        # P0 fix: 不在 POST 时写 heartbeat——否则 worker 20s 内看到 heartbeat 过期就 cancel
        # 让 SSE 连上后第一次 heartbeat 才开始计时
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


@router.post("/{parliament_id}/heartbeat")
async def heartbeat_parliament(
    parliament_id: str,
    req: HeartbeatRequest,
    request: Request,
) -> dict:
    """Renew the client lease; returning home can explicitly keep it alive."""
    now = datetime.now(timezone.utc)
    payload = {
        "last_client_heartbeat": now.isoformat(),
        "keep_alive_until": (
            (now + timedelta(minutes=30)).isoformat()
            if req.keep_running
            else None
        ),
    }
    query = (
        get_supabase()
        .table("parliaments")
        .update(payload)
        .eq("id", parliament_id)
    )
    device_id = _get_device_id(request)
    if device_id:
        query = query.eq("device_id", device_id)
    result = query.execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="议会不存在或无权访问")
    return {"ok": True, "server_time": now.isoformat()}


@router.get("/active/current")
async def get_current_parliament(request: Request) -> dict:
    """Return the latest recoverable council for this anonymous device."""
    device_id = _get_device_id(request)
    if not device_id:
        return {"item": None}
    result = (
        get_supabase()
        .table("parliaments")
        .select("id,topic,status,current_phase,created_at,summary,members_state")
        .eq("device_id", device_id)
        .in_("status", ["queued", "running", "done"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return {"item": result.data[0] if result.data else None}


@router.get("/{parliament_id}")
async def get_parliament(
    parliament_id: str,
    include_agent_traces: bool = True,
) -> ParliamentResponse:
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
    summary = data.get("summary") or {}
    tool_calls = data.get("tool_calls", [])
    stored_traces = []
    if include_agent_traces:
        try:
            stored_events = fetch_events_since(parliament_id, 0, limit=500)
            stored_traces = [
                {"seq": event["seq"], **(event.get("payload") or {})}
                for event in stored_events
                if event.get("event_type") == "agent_trace"
            ]
        except Exception:
            stored_traces = []
    return ParliamentResponse(
        id=data["id"],
        topic=data["topic"],
        status=data["status"],
        created_at=data["created_at"],
        finished_at=data.get("finished_at"),
        summary=data.get("summary"),
        transcript=data.get("transcript", []),
        tool_calls=tool_calls,
        members_state=data.get("members_state", {}),
        selected_expert_ids=(
            data.get("selected_expert_ids")
            or summary.get("selected_expert_ids", [])
        ),
        agent_results=summary.get("agent_results", {}),
        agent_traces=(
            stored_traces
            or [
                item["trace"]
                for item in tool_calls
                if item.get("action") == "agent_trace"
                and isinstance(item.get("trace"), dict)
            ]
            if include_agent_traces
            else []
        ),
        current_phase=data.get("current_phase") or data.get("status") or "queued",
        worker_available=_worker_available(),
        completed_expert_count=len(data.get("members_state") or {}),
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
