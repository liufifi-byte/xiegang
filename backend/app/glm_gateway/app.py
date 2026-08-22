"""GLM Responses Gateway 的 FastAPI 壳。

Codex（model_provider 指向本网关）→ POST /v1/responses
→ 翻译成 Chat Completions → 智谱 GLM 流式接口 → 流式块逐个翻译回
Responses SSE 事件返回给 Codex。

运行：python -m app.glm_gateway（默认 127.0.0.1:8098）。
测试：monkeypatch _chat_stream 即可离线（不打真实 GLM）。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.glm_gateway.translate import (
    chat_chunks_to_response_events,
    translate_request,
)

logger = logging.getLogger("xiegang.glm_gateway")

app = FastAPI(title="Shenmo GLM Responses Gateway", docs_url=None, redoc_url=None)

_chat_client: httpx.AsyncClient | None = None


async def _chat_stream(chat_body: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """调智谱 Chat Completions 流式接口，产出解析后的 chunk 字典。"""
    global _chat_client
    settings = get_settings()
    url = settings.glm_base_url.rstrip("/") + "/chat/completions"
    if _chat_client is None:
        _chat_client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15))
    async with _chat_client.stream(
        "POST",
        url,
        headers={
            "Authorization": f"Bearer {settings.glm_api_key}",
            "Content-Type": "application/json",
        },
        json=chat_body,
    ) as resp:
        if resp.status_code != 200:
            detail = (await resp.aread()).decode("utf-8", errors="replace")[:500]
            logger.warning("GLM 网关上游 %s: %s", resp.status_code, detail)
            raise RuntimeError(f"glm_upstream_{resp.status_code}")
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue


async def _error_events(message: str, model: str) -> AsyncIterator[str]:
    """错误以 Responses 事件形态返回（Codex 侧转为流错误，触发重试/降级）。"""
    from app.glm_gateway.translate import _sse

    yield _sse("response.failed", {
        "type": "response.failed",
        "response": {"id": f"resp_{uuid.uuid4().hex[:12]}", "model": model,
                     "status": "failed", "error": {"message": message}},
    })


@app.post("/v1/responses")
@app.post("/responses")
async def create_response(request: Request) -> StreamingResponse:
    body = await request.json()
    model = str(body.get("model") or get_settings().glm_model)
    chat_body = translate_request(body)
    chat_body["model"] = model  # 网关不做模型改写，Codex 侧配置即所用模型
    response_id = f"resp_{uuid.uuid4().hex[:24]}"

    async def stream() -> AsyncIterator[str]:
        try:
            async for event in chat_chunks_to_response_events(
                _chat_stream(chat_body), response_id=response_id, model=model,
            ):
                yield event
        except Exception as exc:
            logger.warning("网关流失败: %s", exc)
            async for event in _error_events(str(exc)[:200], model):
                yield event

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "provider": "glm-gateway"}


def main() -> None:  # pragma: no cover - 入口
    import os

    import uvicorn

    port = int(os.environ.get("GLM_GATEWAY_PORT", "8098"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    main()
