"""GLM → OpenAI Responses API 翻译层（纯函数，Phase 8 后续：智谱接 Codex）。

Codex 只认 Responses API（wire_api="responses"，上游已删除 chat 分支），
智谱 GLM 说的是 OpenAI Chat Completions 格式。本模块双向翻译：

  translate_request : Responses 请求 → Chat Completions 请求
  chat_chunks_to_response_events : Chat 流式块 → Responses SSE 事件序列

无 IO、无框架依赖——FastAPI 壳在 app.py，单测直接打这里。
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

Role = str


def _content_to_text(content: Any) -> str:
    """Responses content（字符串或 parts 数组）→ 纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in ("input_text", "output_text", "text", "summary_text"):
                    parts.append(str(part.get("text") or ""))
                # input_image 等非文本部件：跳过（GLM 网关首版不透传图像）
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return str(content)


def translate_request(body: dict[str, Any]) -> dict[str, Any]:
    """Responses 请求 → Chat Completions 请求。

    支持的 input item：message / function_call / function_call_output；
    reasoning、web_search_call 等内部条目跳过（GLM 不需要）。
    """
    messages: list[dict[str, Any]] = []
    instructions = body.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": _content_to_text(instructions)})

    raw_input = body.get("input")
    if isinstance(raw_input, str):  # 简化输入："input": "文本"
        messages.append({"role": "user", "content": raw_input})
        raw_input = []

    pending_tool_calls: dict[int, dict[str, Any]] = {}
    for item in raw_input or []:
        if not isinstance(item, dict):
            if isinstance(item, str):  # 简化输入："input": "文本"
                messages.append({"role": "user", "content": item})
            continue
        itype = item.get("type")
        if itype == "message":
            role = item.get("role") or "user"
            if role == "developer":
                role = "system"
            text = _content_to_text(item.get("content"))
            if text or role == "user":
                messages.append({"role": role, "content": text})
        elif itype == "function_call":
            # Responses 的独立条目 → 挂到上一条 assistant 消息的 tool_calls
            call = {
                "id": item.get("call_id") or item.get("id") or f"call_{len(pending_tool_calls)}",
                "type": "function",
                "function": {
                    "name": item.get("name") or "",
                    "arguments": item.get("arguments") or "{}",
                },
            }
            if messages and messages[-1].get("role") == "assistant" and messages[-1].get("tool_calls"):
                messages[-1]["tool_calls"].append(call)
            else:
                messages.append({
                    "role": "assistant",
                    "content": item.get("content") or "",
                    "tool_calls": [call],
                })
        elif itype == "function_call_output":
            messages.append({
                "role": "tool",
                "tool_call_id": item.get("call_id") or "",
                "content": _content_to_text(item.get("output")),
            })
        elif itype == "reasoning":
            continue  # 隐藏推理不透传（spec §15.2 同源原则）
        # web_search_call / 其他：跳过

    chat: dict[str, Any] = {
        "model": body.get("model"),
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if body.get("max_output_tokens") is not None:
        chat["max_tokens"] = body["max_output_tokens"]
    for key in ("temperature", "top_p"):
        if body.get(key) is not None:
            chat[key] = body[key]
    if body.get("parallel_tool_calls") is not None:
        chat["parallel_tool_calls"] = body["parallel_tool_calls"]

    tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description") or "",
                "parameters": t.get("parameters") or {"type": "object", "properties": {}},
            },
        }
        for t in body.get("tools") or []
        if isinstance(t, dict) and t.get("type") == "function" and t.get("name")
    ]
    if tools:
        chat["tools"] = tools
        chat["tool_choice"] = body.get("tool_choice") or "auto"

    return chat


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def chat_chunks_to_response_events(
    chunks: AsyncIterator[dict[str, Any]],
    *,
    response_id: str,
    model: str,
) -> AsyncIterator[str]:
    """Chat 流式块 → Responses SSE 文本流（event: xxx 数据行）。

    事件序列（Codex 解析所需的最小充分集）：
      response.created
      → response.output_item.added(message)
      → response.output_text.delta*
      → response.output_item.done(message)
      → [若有工具调用：output_item.added/done(function_call) × N]
      → response.completed（含 usage）
    """
    yield _sse("response.created", {
        "type": "response.created",
        "response": {"id": response_id, "model": model, "status": "in_progress"},
    })

    item_id = f"msg_{response_id[:8]}"
    text_opened = False
    full_text_parts: list[str] = []

    tool_buffers: dict[int, dict[str, Any]] = {}  # chat index → {id,name,args}
    tool_order: list[int] = []

    usage_payload: dict[str, Any] | None = None
    finish_reason = None

    async for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        if chunk.get("usage"):
            u = chunk["usage"]
            usage_payload = {
                "input_tokens": u.get("prompt_tokens", 0),
                "output_tokens": u.get("completion_tokens", 0),
                "total_tokens": u.get("total_tokens", 0),
            }
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        finish_reason = choice.get("finish_reason") or finish_reason
        delta = choice.get("delta") or {}

        text = delta.get("content")
        if isinstance(text, str) and text:
            if not text_opened:
                text_opened = True
                yield _sse("response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {"id": item_id, "type": "message", "role": "assistant",
                             "status": "in_progress", "content": []},
                })
            full_text_parts.append(text)
            yield _sse("response.output_text.delta", {
                "type": "response.output_text.delta",
                "item_id": item_id,
                "output_index": 0,
                "content_index": 0,
                "delta": text,
            })

        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            buf = tool_buffers.setdefault(idx, {
                "id": "", "name": "", "arguments": "",
            })
            if idx not in tool_order:
                tool_order.append(idx)
            fn = tc.get("function") or {}
            if tc.get("id"):
                buf["id"] = tc["id"]
            if fn.get("name"):
                buf["name"] += fn["name"]
            if fn.get("arguments"):
                buf["arguments"] += fn["arguments"]

    if text_opened:
        full_text = "".join(full_text_parts)
        yield _sse("response.output_item.done", {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": item_id, "type": "message", "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": full_text,
                             "annotations": []}],
            },
        })

    # 工具调用条目（文本之后，保持 output_index 递增）
    output_items: list[dict[str, Any]] = []
    if text_opened:
        output_items.append({"id": item_id, "type": "message", "role": "assistant",
                             "status": "completed",
                             "content": [{"type": "output_text",
                                          "text": "".join(full_text_parts)}]})
    for n, idx in enumerate(tool_order, start=1 if text_opened else 0):
        buf = tool_buffers[idx]
        call_id = buf["id"] or f"call_{response_id[:8]}_{idx}"
        fc_item = {
            "id": f"fc_{call_id}", "type": "function_call",
            "status": "completed", "call_id": call_id,
            "name": buf["name"], "arguments": buf["arguments"] or "{}",
        }
        yield _sse("response.output_item.added", {
            "type": "response.output_item.added", "output_index": n, "item": fc_item,
        })
        yield _sse("response.output_item.done", {
            "type": "response.output_item.done", "output_index": n, "item": fc_item,
        })
        output_items.append(fc_item)

    usage = usage_payload or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    yield _sse("response.completed", {
        "type": "response.completed",
        "response": {
            "id": response_id, "model": model, "status": "completed",
            "output": output_items, "usage": usage,
        },
    })
