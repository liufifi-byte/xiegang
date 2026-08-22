"""事件桥：RuntimeEvent → 产品事件（spec 2026-08-22 §15.1 / §15.2，Phase 1）。

铁律：前端只依赖这里定义的产品事件名与安全 payload，
Codex 原始 Notification 格式绝不穿透（spec §24.9）。
§15.2 过滤原则在 translate 内强制：不带隐藏推理、系统 Prompt、
工具敏感参数、大段原始搜索结果、异常堆栈。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.decision_harness.runtime import RuntimeEvent

# 产品事件全集（spec §15.1）。Phase 1 只会发出去其中一小部分，
# 但枚举在此收口，后续 Phase 不允许前端私加事件名。
PRODUCT_EVENT_TYPES = frozenset(
    {
        "plan_created",
        "plan_revised",
        "user_input_requested",
        "agent_task_created",
        "agent_started",
        "agent_action",
        "agent_tool_started",
        "agent_tool_completed",
        "agent_partial",
        "agent_completed",
        "agent_failed",
        "dispute_opened",
        "debate_message",
        "dispute_resolved",
        "evidence_added",
        "evidence_rejected",
        "audit_started",
        "audit_failed",
        "audit_passed",
        "finalization_proposed",
        "partial_finalized",
        "decision_finalized",
        "runtime_recovered",
        # 通用兜底：中立运行时信息（不含敏感内容，见 translate 的白名单）
        "run_log",
    }
)

# runtime 事件类型 → 产品事件类型。未列出的类型一律降级 run_log。
_RUNTIME_TO_PRODUCT: dict[str, str] = {
    "turn_final": "agent_action",
    "item_completed": "agent_partial",
    "turn_failed": "agent_failed",
}

# agent_partial 允许透传的字段（item 摘要已在 codex_runtime._summarize_item 截断）
_PARTIAL_ALLOWED_FIELDS = ("item_type", "phase", "text")

_MAX_TEXT_CHARS = 600


def _clip(text: str, limit: int = _MAX_TEXT_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def translate_runtime_event(run_id: str, event: RuntimeEvent) -> dict[str, Any] | None:
    """中立 RuntimeEvent → 产品事件 dict；不需要给用户看的返回 None。

    返回形状与 parliament_events 行对齐：
    {run_id, seq, event_type, payload, thread_id, created_at}
    """
    product_type = _RUNTIME_TO_PRODUCT.get(event.type)
    payload: dict[str, Any]

    if product_type == "agent_action":
        # 主席动作：final_response 是主 Agent 输出的 JSON 信封（或纯文本）
        final = event.payload.get("final_response")
        payload = {"agent": "main"}
        if isinstance(final, str):
            payload["raw"] = _clip(final)
        else:
            payload["raw"] = final
    elif product_type == "agent_partial":
        item = event.payload.get("item")
        if isinstance(item, dict):
            payload = {
                "agent": "main",
                **{k: v for k, v in item.items() if k in _PARTIAL_ALLOWED_FIELDS},
            }
            if isinstance(payload.get("text"), str):
                payload["text"] = _clip(payload["text"])
        else:
            return None  # 无有效内容：直接吞掉
        if not payload.get("text"):
            return None  # 非文本 item（工具调用等）：Phase 1 不外露
    elif product_type == "agent_failed":
        payload = {
            "agent": "main",
            "message": _clip(str(event.payload.get("message", "运行时错误"))),
            "turn_id": event.payload.get("turn_id"),
        }
    else:
        # 未映射类型：只有显式白名单外但明确安全的才记 run_log；
        # runtime_notification 可能带任意 SDK payload —— 一律丢弃。
        return None

    return {
        "run_id": run_id,
        "seq": event.seq,
        "event_type": product_type,
        "payload": payload,
        "thread_id": event.thread_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def make_product_event(
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    seq: int = 0,
) -> dict[str, Any]:
    """Workflow 层主动发产品事件（plan_created / user_input_requested 等）。

    event_type 必须在 PRODUCT_EVENT_TYPES 内——编译期收口，防止拼写漂移。
    """
    if event_type not in PRODUCT_EVENT_TYPES:
        raise ValueError(f"未知产品事件类型: {event_type!r}（必须在 §15.1 清单内）")
    return {
        "run_id": run_id,
        "seq": seq,
        "event_type": event_type,
        "payload": payload,
        "thread_id": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
