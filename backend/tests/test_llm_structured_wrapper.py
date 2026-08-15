"""_StructuredWrapper 的 None 守卫与强制 tool_choice 回归。

事故背景（议会 7e8f068e，线上 11/13 辩论降级）：服务器侧 langchain-openai 的
with_structured_output 在模型不调工具时**返回 None 而非抛异常**，旧 wrapper 把
None 当成功放行 → round_debate 的 stmt.stance 直接 AttributeError。
修复 = fast 路径强制 tool_choice + 原生结果 None 一律走 fallback。
"""
import asyncio
from types import SimpleNamespace

import app.llm as llm_module
from app.llm import get_structured_llm
from pydantic import BaseModel


class Probe(BaseModel):
    """探测 schema。"""

    risk: str
    severity: int


_JSON = '{"risk": "竞业赔偿风险", "severity": 9}'


def _make_wrapper(monkeypatch, *, forced_resp, structured_resp, plain_content):
    """构造 wrapper，底层 LLM / forced / structured / plain 全部打桩。"""

    async def _plain_ainvoke(prompt, **kwargs):
        return SimpleNamespace(content=plain_content)

    plain = SimpleNamespace(
        invoke=lambda prompt, **kwargs: SimpleNamespace(content=plain_content),
        ainvoke=_plain_ainvoke,
    )

    async def _forced_ainvoke(prompt, **kwargs):
        return forced_resp

    async def _structured_ainvoke(prompt, **kwargs):
        return structured_resp

    stub_llm = SimpleNamespace(
        invoke=plain.invoke,
        ainvoke=plain.ainvoke,
        bind_tools=lambda tools, tool_choice=None: SimpleNamespace(
            invoke=lambda prompt, **kwargs: forced_resp,
            ainvoke=_forced_ainvoke,
        ),
        with_structured_output=lambda schema, **kwargs: SimpleNamespace(
            invoke=lambda prompt, **kwargs: structured_resp,
            ainvoke=_structured_ainvoke,
        ),
    )
    monkeypatch.setattr(llm_module, "get_llm", lambda **kwargs: stub_llm)
    return get_structured_llm(Probe, temperature=0.3, fast=True)


def test_fast_parses_forced_tool_call(monkeypatch):
    wrapper = _make_wrapper(
        monkeypatch,
        forced_resp=SimpleNamespace(
            tool_calls=[{"name": "Probe", "args": {"risk": "x", "severity": 3}}]
        ),
        structured_resp=None,
        plain_content=_JSON,
    )
    result = wrapper.invoke("prompt")
    assert result.severity == 3


def test_fast_falls_back_when_model_skips_tool(monkeypatch):
    """模型回散文不调工具 → 不能返回 None，必须走 fallback 解析出结构。"""
    wrapper = _make_wrapper(
        monkeypatch,
        forced_resp=SimpleNamespace(content="我不想调用工具", tool_calls=[]),
        structured_resp=None,
        plain_content=_JSON,
    )
    result = wrapper.invoke("prompt")
    assert result.risk == "竞业赔偿风险"
    assert result.severity == 9


def test_fast_async_falls_back_when_model_skips_tool(monkeypatch):
    wrapper = _make_wrapper(
        monkeypatch,
        forced_resp=SimpleNamespace(tool_calls=[]),
        structured_resp=None,
        plain_content=_JSON,
    )
    result = asyncio.run(wrapper.ainvoke("prompt"))
    assert result.severity == 9


def test_native_none_result_falls_back(monkeypatch):
    """fast=False 时原生 structured 返回 None → 也必须走 fallback（同步版）。"""
    wrapper = _make_wrapper(
        monkeypatch,
        forced_resp=None,
        structured_resp=None,  # 原生返回 None
        plain_content=_JSON,
    )
    wrapper.fast = False  # 强制走原生分支
    result = wrapper.invoke("prompt")
    assert result.severity == 9


def test_native_none_result_falls_back_async(monkeypatch):
    wrapper = _make_wrapper(
        monkeypatch,
        forced_resp=None,
        structured_resp=None,
        plain_content=_JSON,
    )
    wrapper.fast = False
    result = asyncio.run(wrapper.ainvoke("prompt"))
    assert result.risk == "竞业赔偿风险"
