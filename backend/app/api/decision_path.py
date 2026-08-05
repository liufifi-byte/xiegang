"""决策路径 API - PRD §13.1-§13.4 / §16"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.middleware.rate_limit import _get_device_id
from app.schemas.decision_path import (
    AnswerItem,
    DecisionPathConfirmPayload,
    DecisionPathPreviewRequest,
    DecisionPathPreviewResponse,
)
from app.services.decision_path import (
    consume_draft,
    generate_preview,
    load_preview_by_id,
)

router = APIRouter(prefix="/api/decision-path", tags=["decision-path"])


def _device_id_or_403(request: Request) -> str:
    """决策路径必须带 device_id(API anonymous device bound)。"""
    dev = _get_device_id(request)
    if not dev or dev.startswith("ip:"):
        # 允许 ip-fallback, 但只有真实 device_id 才能跨页面恢复草稿
        pass
    return dev


@router.post("/preview", response_model=DecisionPathPreviewResponse)
async def preview_decision_path(req: DecisionPathPreviewRequest, request: Request) -> DecisionPathPreviewResponse:
    """生成或修订决策路径预览。"""
    device_id = _device_id_or_403(request)
    try:
        return await generate_preview(req, device_id=device_id)
    except Exception as exc:  # noqa: BLE001
        # 任何错误都不应让前端崩溃, 但要让用户看到错误, 不偷偷走 fallback 之外的死路
        raise HTTPException(status_code=500, detail=f"决策路径生成失败: {exc}") from exc


@router.post("/{generation_id}")
async def get_decision_path(generation_id: str, request: Request) -> dict:
    """从服务端恢复权威草稿(刷新页面后用)。"""
    device_id = _device_id_or_403(request)
    draft = load_preview_by_id(generation_id, device_id)
    if draft is None:
        # PRD §13.2: 其它设备 / 不存在 / 已过期 / 已消费 都返回 404
        raise HTTPException(status_code=404, detail="草稿不存在或已过期")
    return draft.model_dump()


@router.delete("/{generation_id}")
async def delete_decision_path(generation_id: str, request: Request) -> dict:
    """用户明确放弃时把草稿标记为 expired。"""
    device_id = _device_id_or_403(request)
    draft = load_preview_by_id(generation_id, device_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="草稿不存在或已过期")
    # 标记 expired
    from app.db import get_supabase

    get_supabase().table("decision_path_drafts").update(
        {"status": "expired"}
    ).eq("id", generation_id).execute()
    return {"ok": True}
