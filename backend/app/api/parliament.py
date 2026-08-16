"""议会 HTTP API。"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import get_settings
from app.db import get_supabase
from app.event_store import fetch_events_since
from app.event_store import append_event
from app.middleware.rate_limit import (
    _check_and_record,
    _get_client_ip,
    _get_device_id,
)
from app.runtime_context import request_cancel, set_user_interrupt
from app.schemas.parliament import (
    CreateParliamentRequest,
    ParliamentResponse,
    SubmitClarificationRequest,
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


def _uuid_or_none(value: str | None) -> str | None:
    """device_id 是合法 UUID 才能落 user_id（user_memories.user_id 是 uuid 列）。

    浏览器前端发的 X-Device-Id 是 UUID；ip:1.2.3.4 这类 fallback 设备标识
    不是 UUID，宁可留 None（memory_search 不下发）也不能炸 insert。
    """
    if not value:
        return None
    try:
        import uuid as _uuid

        return str(_uuid.UUID(value))
    except (ValueError, AttributeError):
        return None


@router.post("")
async def create_parliament(req: CreateParliamentRequest, request: Request) -> dict:
    """创建议会，返回议会 ID 和 SSE 流 URL。

    P0-S1.1: 加了 IP + device_id 双轨限流。
    P0-S2 fix: 开发环境（APP_ENV=development）跳过限流方便测试。
    PRD v2: 如果带了 decision_path_generation_id, 校验确认 + consume 草稿 +
            把 decision_path bundle 写入 parliaments.decision_path。
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

    # PRD v2 §13.4: 决策路径校验 + consume
    decision_path_payload: dict = {}
    decision_path_status = "legacy"
    decision_path_confirmed_at = None
    if req.decision_path_generation_id:
        if req.brief_confirmation != "confirmed":
            raise HTTPException(
                status_code=400,
                detail="使用 decision path 时必须 brief_confirmation=confirmed",
            )
        from app.schemas.decision_path import AnswerItem
        from app.services.decision_path import (
            build_expert_payload_from_draft,
            consume_draft,
            load_preview_by_id,
        )
        from datetime import datetime, timezone

        draft = load_preview_by_id(req.decision_path_generation_id, device_id)
        if draft is None:
            raise HTTPException(
                status_code=404,
                detail="决策路径草稿不存在、已过期或已被使用",
            )
        # 校验所有问题都有答案(包括 unknown)
        answers = [
            AnswerItem(question_id=a.question_id, value=a.value, custom_value=a.custom_value)
            for a in req.decision_path_answers
        ]
        answered_ids = {a.question_id for a in answers}
        required_ids = {q.id for q in draft.questions}
        missing = required_ids - answered_ids
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"以下问题必须回答或选择不确定: {', '.join(sorted(missing))}",
            )
        # 构造专家 context payload
        decision_path_payload = build_expert_payload_from_draft(draft, answers)
        # PRD §13.2: 事务内 consume(简化版)
        ok = consume_draft(req.decision_path_generation_id, device_id)
        if not ok:
            raise HTTPException(
                status_code=409,
                detail="决策路径草稿已被使用或失效，请重新生成",
            )
        decision_path_status = "confirmed"
        decision_path_confirmed_at = datetime.now(timezone.utc).isoformat()

    supabase = get_supabase()

    # PRD §13.4 / §6.7: 幂等键去重(同一 idempotency_key 已存在会议则返回原议会)
    if req.idempotency_key:
        existing = (
            supabase.table("parliaments")
            .select("id")
            .eq("idempotency_key", req.idempotency_key)
            .limit(1)
            .execute()
        )
        if existing.data:
            old_id = existing.data[0]["id"]
            return {
                "parliament_id": old_id,
                "stream_url": f"/api/parliament/{old_id}/stream",
                "device_id": device_id,
                "idempotent_reuse": True,
            }

    row = {
        "topic": req.topic,
        "topic_category": req.topic_category,
        "user_background": req.user_background,
        # V2-S3 补全：user_id 缺省回落到设备 UUID——跨议会记忆（memory_search
        # 的下发条件 + user_memories 的读写键）从此对浏览器用户默认生效。
        "user_id": req.user_id or _uuid_or_none(device_id),
        "device_id": device_id,  # P0-S2: 所有权校验用
        "status": "queued",     # P0-S2: Worker 抢任务用
        "members_state": {},
        "transcript": [],
        "tool_calls": [],
        "selected_expert_ids": req.selected_expert_ids,
        "current_phase": "queued",
        # P0 fix: 不在 POST 时写 heartbeat——否则 worker 20s 内看到 heartbeat 过期就 cancel
        # 让 SSE 连上后第一次 heartbeat 才开始计时
        # PRD v2 新字段:
        "decision_path": decision_path_payload,
        "decision_path_status": decision_path_status,
        "decision_path_confirmed_at": decision_path_confirmed_at,
        "idempotency_key": req.idempotency_key,
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


@router.post("/{parliament_id}/clarification")
async def submit_clarification(
    parliament_id: str,
    req: SubmitClarificationRequest,
    request: Request,
) -> dict:
    """Save the one-shot clarification and requeue the council atomically."""
    supabase = get_supabase()
    result = (
        supabase.table("parliaments")
        .select(
            "id,status,device_id,user_background,transcript,"
            "clarification_status,clarification_questions,clarification_answers"
        )
        .eq("id", parliament_id)
        .single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="议会不存在")
    row = result.data
    device_id = _get_device_id(request)
    if row.get("device_id") and row.get("device_id") != device_id:
        raise HTTPException(status_code=404, detail="议会不存在或无权访问")

    current_status = row.get("clarification_status") or "not_checked"
    if current_status in {"answered", "skipped"}:
        return {
            "ok": True,
            "parliament_id": parliament_id,
            "clarification_status": current_status,
            "answers": row.get("clarification_answers") or [],
            "already_submitted": True,
        }
    if current_status != "awaiting_input" or row.get("status") != "awaiting_input":
        raise HTTPException(status_code=409, detail="本场会议当前不在等待补充")

    questions = list(row.get("clarification_questions") or [])
    questions_by_id = {
        str(question.get("id")): question
        for question in questions
        if question.get("id")
    }
    normalized_answers: list[dict] = []
    if req.action == "answer":
        provided = {answer.question_id: answer.value for answer in req.answers}
        if set(provided) != set(questions_by_id):
            raise HTTPException(status_code=400, detail="请回答全部问题，或选择暂时不知道")
        for question_id, question in questions_by_id.items():
            value = provided[question_id]
            text_value = str(value).strip()
            if not text_value:
                raise HTTPException(status_code=400, detail="回答不能为空")
            answer_type = question.get("answer_type")
            if answer_type == "single_choice":
                options = [str(item) for item in question.get("options") or []]
                if text_value not in options:
                    raise HTTPException(status_code=400, detail="单选答案无效")
            elif answer_type == "number":
                try:
                    float(value)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="请输入有效数字")
            elif len(text_value) > 500:
                raise HTTPException(status_code=400, detail="回答过长")
            normalized_answers.append(
                {
                    "question_id": question_id,
                    "question": question.get("question") or "",
                    "value": text_value,
                    "status": (
                        "unknown"
                        if "不确定" in text_value or "不知道" in text_value
                        else "answered"
                    ),
                }
            )
        next_status = "answered"
        readable = "；".join(
            f"{item['question']}：{item['value']}"
            for item in normalized_answers
        )
        transcript_content = f"你补充了：{readable}"
    else:
        normalized_answers = [
            {
                "question_id": question_id,
                "question": question.get("question") or "",
                "value": "",
                "status": "unknown",
            }
            for question_id, question in questions_by_id.items()
        ]
        next_status = "skipped"
        readable = "；".join(
            item["question"] for item in normalized_answers
        )
        transcript_content = (
            "你选择暂时不知道，会议将按未知条件继续分析："
            f"{readable}"
        )

    previous_background = str(row.get("user_background") or "").strip()
    background_addition = (
        f"用户在会议开始前补充：{readable}"
        if req.action == "answer"
        else f"用户暂时无法确认以下条件：{readable}"
    )
    merged_background = "\n".join(
        part for part in (previous_background, background_addition) if part
    )
    transcript = list(row.get("transcript") or [])
    transcript.append(
        {
            "speaker": "user",
            "round": 0,
            "type": "user_clarification",
            "content": transcript_content,
            "timestamp": "刚刚",
        }
    )
    now = datetime.now(timezone.utc).isoformat()
    update_result = (
        supabase.table("parliaments")
        .update(
            {
                "status": "queued",
                "current_phase": "queued",
                "clarification_status": next_status,
                "clarification_answers": normalized_answers,
                "clarification_answered_at": now,
                "user_background": merged_background,
                "transcript": transcript,
                "error_message": None,
            }
        )
        .eq("id", parliament_id)
        .eq("clarification_status", "awaiting_input")
        .execute()
    )
    if not update_result.data:
        latest = (
            supabase.table("parliaments")
            .select("clarification_status,clarification_answers")
            .eq("id", parliament_id)
            .single()
            .execute()
        )
        if latest.data and latest.data.get("clarification_status") in {
            "answered",
            "skipped",
        }:
            return {
                "ok": True,
                "parliament_id": parliament_id,
                "clarification_status": latest.data["clarification_status"],
                "answers": latest.data.get("clarification_answers") or [],
                "already_submitted": True,
            }
        raise HTTPException(status_code=409, detail="会议状态已经发生变化")
    try:
        append_event(
            parliament_id,
            (
                "clarification_answered"
                if req.action == "answer"
                else "clarification_skipped"
            ),
            {
                "clarification_status": next_status,
                "answers": normalized_answers,
                "entry": transcript[-1],
            },
        )
        append_event(
            parliament_id,
            "clarification_resumed",
            {"current_phase": "queued"},
        )
    except Exception:
        pass
    return {
        "ok": True,
        "parliament_id": parliament_id,
        "clarification_status": next_status,
        "answers": normalized_answers,
        "already_submitted": False,
    }


@router.post("/{parliament_id}/cancel")
async def cancel_parliament(parliament_id: str) -> dict:
    """V1-S4 / V2-S2: 请求取消议会。

    设置 cancel_requested 标记，SSE 流每次 yield 检查并提前 break。
    V2-S2: 同时遍历该议会所有 in-flight LLM 调用，task.cancel() 硬中断。
    """
    from app.cancel_guard import cancel_all_inflight

    try:
        row = (
            get_supabase()
            .table("parliaments")
            .select("status")
            .eq("id", parliament_id)
            .single()
            .execute()
        )
        row_data = row.data
    except Exception:
        # Tests and transient DB failures can still use the established
        # runtime-context cancellation path below.
        row_data = None
    if row_data and row_data.get("status") == "awaiting_input":
        now = datetime.now(timezone.utc).isoformat()
        (
            get_supabase()
            .table("parliaments")
            .update(
                {
                    "status": "cancelled",
                    "current_phase": "cancelled",
                    "finished_at": now,
                    "error_message": "cancelled_by_user",
                }
            )
            .eq("id", parliament_id)
            .eq("status", "awaiting_input")
            .execute()
        )
        try:
            append_event(parliament_id, "cancelled", {"reason": "cancelled_by_user"})
        except Exception:
            pass
        return {
            "ok": True,
            "parliament_id": parliament_id,
            "message": "等待补充的会议已结束",
            "cancelled_tasks": 0,
        }

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


@router.post("/active/current")
async def get_current_parliament(request: Request) -> dict:
    """Return the latest recoverable council for this anonymous device."""
    device_id = _get_device_id(request)
    if not device_id:
        return {"item": None}
    result = (
        get_supabase()
        .table("parliaments")
        .select(
            "id,topic,status,current_phase,created_at,summary,members_state,"
            "clarification_status,clarification_questions"
        )
        .eq("device_id", device_id)
        .in_("status", ["queued", "running", "awaiting_input", "done"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return {"item": result.data[0] if result.data else None}


@router.post("/{parliament_id}")
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
        clarification_status=data.get("clarification_status") or "not_checked",
        clarification_questions=data.get("clarification_questions") or [],
        clarification_answers=data.get("clarification_answers") or [],
    )


@router.post("/")
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
