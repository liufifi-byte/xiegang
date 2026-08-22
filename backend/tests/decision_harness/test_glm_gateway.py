"""GLM→Responses 网关测试：翻译层、端点、provider 配置。

含皇冠集成测试：真实 codex-app-server 二进制 + 本地网关（注入假 GLM 流）
→ CodexAgentRuntime 驱动完整 turn 并拿到与假 GLM 一致的最终回答。
"""
import asyncio
import json
import os
from pathlib import Path

import pytest

from app.config import get_settings
from app.glm_gateway import translate as T
from app.glm_gateway import provider as P


# ============ translate_request ============

def test_translate_request_full_shape():
    body = {
        "model": "glm-5.2",
        "instructions": "你是决策主席",
        "input": [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "评估这个选择"}]},
            {"type": "function_call", "name": "research.search",
             "arguments": "{\"q\": \"现金流\"}", "call_id": "call_1"},
            {"type": "function_call_output", "call_id": "call_1",
             "output": "结果：可支撑"},
            {"type": "reasoning", "summary": "内部推理不该出现"},
            {"type": "message", "role": "developer", "content": "补充守则"},
        ],
        "tools": [{
            "type": "function", "name": "research.search",
            "description": "检索", "parameters": {"type": "object",
                                                  "properties": {"q": {"type": "string"}}},
        }],
        "tool_choice": "auto",
        "max_output_tokens": 1000,
        "temperature": 0.3,
    }
    chat = T.translate_request(body)
    assert chat["model"] == "glm-5.2"
    assert chat["messages"][0] == {"role": "system", "content": "你是决策主席"}
    assert chat["messages"][1]["content"] == "评估这个选择"
    # function_call 挂到 assistant tool_calls
    assistant = chat["messages"][2]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert assistant["tool_calls"][0]["function"]["name"] == "research.search"
    # 输出回填为 tool 消息
    assert chat["messages"][3] == {"role": "tool", "tool_call_id": "call_1",
                                   "content": "结果：可支撑"}
    # developer → system；reasoning 跳过
    assert chat["messages"][4]["role"] == "system"
    assert not any("内部推理" in json.dumps(m, ensure_ascii=False)
                   for m in chat["messages"])
    # tools 转换
    assert chat["tools"][0]["function"]["name"] == "research.search"
    assert chat["max_tokens"] == 1000 and chat["temperature"] == 0.3


def test_translate_request_plain_string_input():
    chat = T.translate_request({"model": "m", "input": "直接文本"})
    assert chat["messages"] == [{"role": "user", "content": "直接文本"}]


# ============ chat_chunks_to_response_events ============

def _parse_sse(text: str):
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        events.append((event, data))
    return events


async def _gen(chunks):
    for c in chunks:
        yield c


async def test_text_stream_event_sequence():
    chunks = [
        {"choices": [{"delta": {"content": "现金"}}]},
        {"choices": [{"delta": {"content": "流可控"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                  "total_tokens": 15}},
    ]
    text = ""
    async for sse in T.chat_chunks_to_response_events(
        _gen(chunks), response_id="resp_test", model="glm-5.2"
    ):
        text += sse
    events = _parse_sse(text)
    names = [e for e, _ in events]
    assert names == ["response.created", "response.output_item.added",
                     "response.output_text.delta", "response.output_text.delta",
                     "response.output_item.done", "response.completed"]
    assert events[2][1]["delta"] == "现金"
    done_item = events[4][1]["item"]
    assert done_item["content"][0]["text"] == "现金流可控"
    completed = events[5][1]["response"]
    assert completed["usage"]["input_tokens"] == 10
    assert completed["output"][0]["content"][0]["text"] == "现金流可控"


async def test_tool_call_stream_assembles_fragments():
    chunks = [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_glm1", "function": {"name": "research.",
                                                          "arguments": "{\"q\":"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"name": "search",
                                      "arguments": "\"现金流\"}"}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    text = ""
    async for sse in T.chat_chunks_to_response_events(
        _gen(chunks), response_id="resp_tool", model="glm-5.2"
    ):
        text += sse
    events = _parse_sse(text)
    done = [d for e, d in events if e == "response.output_item.done"][0]
    item = done["item"]
    assert item["type"] == "function_call"
    assert item["call_id"] == "call_glm1"
    assert item["name"] == "research.search"  # 分片名拼接
    assert json.loads(item["arguments"]) == {"q": "现金流"}


# ============ FastAPI 端点（注入假 GLM） ============

async def _endpoint_translates_and_streams(monkeypatch):
    from app.glm_gateway import app as gateway_app

    captured = {}

    async def fake_chat(chat_body):
        captured.update(chat_body)
        for c in [{"choices": [{"delta": {"content": "网关"}}]},
                  {"choices": [{"delta": {"content": "回声"}}]},
                  {"choices": [{"delta": {}, "finish_reason": "stop"}]}]:
            yield c

    monkeypatch.setattr(gateway_app, "_chat_stream", fake_chat)
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway_app.app),
        base_url="http://test",
    ) as client:
        resp = await client.post("/v1/responses", json={
            "model": "glm-5.2", "instructions": "sys", "input": "hi",
        })
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(resp.text)
        assert events[-1][0] == "response.completed"
        assert "网关回声" in resp.text
        # 请求确实被翻译成了 chat 格式发往“GLM”
        assert captured["messages"][0] == {"role": "system", "content": "sys"}
        assert captured["stream"] is True
        health = await client.get("/healthz")
        assert health.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_gateway_endpoint(monkeypatch):
    await _endpoint_translates_and_streams(monkeypatch)


# ============ provider（CODEX_HOME 生成） ============

def test_write_codex_home_and_build_env(tmp_path):
    settings = get_settings()
    home = P.write_codex_home(home=tmp_path / "ch",
                              gateway_url="http://127.0.0.1:9099",
                              model="glm-5.2")
    cfg = (Path(home) / "config.toml").read_text()
    assert 'base_url = "http://127.0.0.1:9099/v1"' in cfg
    assert 'wire_api = "responses"' in cfg
    assert 'model = "glm-5.2"' in cfg
    assert "env_key = \"SHENMO_GLM_KEY\"" in cfg

    kwargs = P.build_glm_codex_env(
        codex_bin="/bin/codex-app-server",
        gateway_url="http://127.0.0.1:9099", model="glm-5.2",
        home=tmp_path / "ch2",
    )
    assert kwargs["launch_args_override"] == ("/bin/codex-app-server",
                                              "--listen", "stdio://")
    assert kwargs["env"]["CODEX_HOME"].endswith("ch2")
    assert kwargs["env"]["SHENMO_GLM_KEY"] == "set"  # 不泄真实 key 到子进程
    assert kwargs["client_name"] == "shenmo_harness"
    _ = settings  # 保留引用：glm_api_key 在 build 内部使用


# ============ 皇冠：真二进制 + 网关 + 假 GLM 全链路 ============

CODEX_BIN = os.environ.get(
    "CODEX_BIN",
    "/Users/xinyiliu/Desktop/codex-fork/codex-rs/target/debug/codex-app-server",
)


@pytest.mark.skipif(not os.path.exists(CODEX_BIN), reason="自建 codex-app-server 不存在")
def test_real_codex_turn_through_glm_gateway(monkeypatch, tmp_path):
    """CodexAgentRuntime → 真实 codex-app-server → 本地网关 → 假 GLM。"""
    pytest.importorskip("openai_codex")

    import httpx
    import uvicorn
    from app.glm_gateway import app as gateway_app
    from app.decision_harness.codex_runtime import CodexAgentRuntime

    async def fake_chat(chat_body):
        reply = "现金流可以支撑 12 个月，但容错率不足 3 个月。"
        yield {"choices": [{"delta": {"content": reply[:10]}}]}
        yield {"choices": [{"delta": {"content": reply[10:]}}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        yield {"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 6,
                                        "total_tokens": 14}}

    monkeypatch.setattr(gateway_app, "_chat_stream", fake_chat)

    server = uvicorn.Server(uvicorn.Config(
        gateway_app.app, host="127.0.0.1", port=8097, log_level="error",
    ))
    import threading

    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):  # 等网关就绪
        if server.started:
            break
        import time
        time.sleep(0.1)
    assert server.started, "网关未能在 10s 内启动"

    settings = get_settings()
    monkeypatch.setattr(settings, "codex_use_glm_gateway", True, raising=False)
    monkeypatch.setattr(settings, "glm_gateway_url", "http://127.0.0.1:8097",
                        raising=False)
    monkeypatch.setattr(settings, "codex_bin", CODEX_BIN, raising=False)

    async def scenario():
        rt = CodexAgentRuntime()
        session = "glm-itest"
        events = []
        await rt.start_session(session, instructions="你是测试主席。")
        async for ev in rt.start_turn(session, "评估现金流"):
            events.append(ev)
        await rt.close_session(session)
        await rt.aclose()
        return events

    events = asyncio.run(scenario())
    finals = [e.payload.get("final_response") for e in events
              if e.type == "turn_final"]
    assert finals and "现金流可以支撑 12 个月" in finals[0]
    assert events[-1].type == "turn_completed"
