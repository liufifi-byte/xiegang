"""event_bridge 测试：中立事件 → §15.1 产品事件，§15.2 内容过滤。"""
import pytest

from app.decision_harness.event_bridge import (
    PRODUCT_EVENT_TYPES,
    make_product_event,
    translate_runtime_event,
)
from app.decision_harness.runtime import RuntimeEvent


def _ev(type_: str, payload: dict | None = None) -> RuntimeEvent:
    return RuntimeEvent(seq=1, type=type_, payload=payload or {}, thread_id="th")


def test_turn_final_maps_to_agent_action():
    out = translate_runtime_event("r1", _ev("turn_final", {"final_response": '{"action":"ask_user"}'}))
    assert out["event_type"] == "agent_action"
    assert out["run_id"] == "r1"
    assert out["payload"]["agent"] == "main"
    assert out["payload"]["raw"] == '{"action":"ask_user"}'


def test_agent_partial_only_for_text_items():
    out = translate_runtime_event("r1", _ev("item_completed", {"item": {
        "item_type": "AgentMessageThreadItem", "phase": "commentary", "text": "正在评估",
    }}))
    assert out["event_type"] == "agent_partial"
    assert out["payload"]["text"] == "正在评估"

    # 非文本 item（工具调用等）不外露
    assert translate_runtime_event("r1", _ev("item_completed", {"item": {
        "item_type": "CommandExecutionThreadItem",
    }})) is None
    # 无 item 字段 → 吞掉
    assert translate_runtime_event("r1", _ev("item_completed", {})) is None


def test_raw_sdk_notification_never_leaks():
    """未映射类型（含任意 SDK payload）必须被丢弃，不得到达前端。"""
    assert translate_runtime_event("r1", _ev("runtime_notification", {
        "method": "item/updated", "payload": {"command": "rm -rf /", "env": "secret"},
    })) is None
    assert translate_runtime_event("r1", _ev("turn_completed", {"turn_id": "t"})) is None


def test_long_text_clipped():
    out = translate_runtime_event("r1", _ev("item_completed", {"item": {
        "item_type": "AgentMessageThreadItem", "text": "长" * 5000,
    }}))
    assert len(out["payload"]["text"]) <= 601


def test_agent_failed_message_clipped():
    out = translate_runtime_event("r1", _ev("turn_failed", {
        "message": "E" * 3000, "turn_id": "t9",
    }))
    assert out["event_type"] == "agent_failed"
    assert len(out["payload"]["message"]) <= 601


def test_make_product_event_enumerates_spec_15_1():
    assert "plan_created" in PRODUCT_EVENT_TYPES
    assert "decision_finalized" in PRODUCT_EVENT_TYPES
    assert "runtime_recovered" in PRODUCT_EVENT_TYPES
    # §15.1 清单 + run_log 兜底，共 24 项（23 spec + run_log）
    assert len(PRODUCT_EVENT_TYPES) == 24

    ev = make_product_event("r1", "user_input_requested", {"questions": ["预算多少？"]})
    assert ev["event_type"] == "user_input_requested"

    with pytest.raises(ValueError, match="§15.1"):
        make_product_event("r1", "codex_item_completed", {})
