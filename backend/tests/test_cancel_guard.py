"""V2-S2 cancel_guard + ainvoke 测试。"""
import asyncio

import pytest

from app.cancel_guard import (
    _clear_all_for_tests,
    cancel_all_inflight,
    get_inflight_count,
    guard_ainvoke,
)


@pytest.fixture(autouse=True)
def clean():
    _clear_all_for_tests()
    yield
    _clear_all_for_tests()


def test_guard_no_cancel_normal_completion():
    """正常完成的 coro 不被中断。"""
    async def slow_fn():
        await asyncio.sleep(0.05)
        return "done"

    async def runner():
        return await guard_ainvoke("p1", slow_fn(), timeout=2)

    result = asyncio.run(runner())
    assert result == "done"


def test_guard_timeout_triggers_cancel():
    """超时会触发 TimeoutError。"""
    async def hang():
        await asyncio.sleep(5)
        return "should-not-reach"

    async def runner():
        return await guard_ainvoke("p1", hang(), timeout=0.1)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(runner())


def test_cancel_all_inflight_during_ainvoke():
    """cancel_all_inflight 应该中断在飞的 guard_ainvoke。"""
    started = asyncio.Event()
    cancelled = {"flag": False}

    async def slow():
        started.set()
        try:
            await asyncio.sleep(10)
            return "should-not-reach"
        except asyncio.CancelledError:
            cancelled["flag"] = True
            raise

    async def runner():
        # 并发：guard_ainvoke + 0.2s 后 cancel
        task = asyncio.ensure_future(guard_ainvoke("p1", slow(), timeout=10))
        await started.wait()
        # 确认有 1 个 inflight
        await asyncio.sleep(0.05)
        n = get_inflight_count("p1")
        assert n == 1
        cancelled_n = await cancel_all_inflight("p1")
        assert cancelled_n == 1
        # 等被 cancel
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled["flag"] is True

    asyncio.run(runner())


def test_get_inflight_count_returns_0_when_empty():
    assert get_inflight_count("no-id") == 0


def test_register_cleanup_after_done():
    """完成后 inflight 注册应该清空。"""
    async def quick():
        await asyncio.sleep(0.02)
        return "ok"

    async def runner():
        await guard_ainvoke("p1", quick(), timeout=2)

    asyncio.run(runner())
    assert get_inflight_count("p1") == 0


# ============ ainvoke wrapper 测试 ============

def test_structured_wrapper_has_ainvoke():
    """V2-S2: _StructuredWrapper 有 ainvoke 方法。"""
    from app.llm import _StructuredWrapper
    from app.schemas.member import MemberStatement

    # 不调构造（避免真创建 LLM 客户端），只验证方法存在
    assert hasattr(_StructuredWrapper, "ainvoke"), "ainvoke 方法缺失"


def test_structured_wrapper_ainvoke_uses_native(monkeypatch):
    """V2-S2: ainvoke 成功路径走原生 structured.ainvoke，不走 fallback。"""
    from app.llm import _StructuredWrapper, get_llm
    from app.schemas.member import MemberStatement

    class MockStructured:
        async def ainvoke(self, prompt, **kwargs):
            return MemberStatement(
                member_id="x", stance="cautious", confidence=0.8,
                evidence=[], reasoning_summary="原生异步路径",
            )
        def invoke(self, prompt, **kwargs):
            raise AssertionError("invoke 不应该被调用（V2-S2 走 ainvoke）")

    class MockLLM:
        def with_structured_output(self, schema):
            return MockStructured()

    monkeypatch.setattr("app.llm.get_llm", lambda **kw: MockLLM())

    wrapper = _StructuredWrapper(MemberStatement, temperature=0.5)
    wrapper.llm = MockLLM()
    wrapper.structured = MockStructured()

    async def runner():
        return await wrapper.ainvoke("any prompt")

    result = asyncio.run(runner())
    assert result.reasoning_summary == "原生异步路径"


def test_structured_wrapper_ainvoke_fallback_on_structured_failure(monkeypatch):
    """V2-S2: 原生 ainvoke 失败时降级到 llm.ainvoke + JSON 抽取。"""
    import json
    from app.llm import _StructuredWrapper
    from app.schemas.member import MemberStatement

    valid_json = json.dumps({
        "member_id": "suan", "stance": "oppose", "confidence": 0.7,
        "evidence": [], "reasoning_summary": "fallback 路径",
    })

    class MockStructured:
        async def ainvoke(self, prompt, **kwargs):
            raise ValueError("原生失败")

    class MockRawResponse:
        content = f"```json\n{valid_json}\n```"

    class MockLLM:
        def with_structured_output(self, schema):
            return MockStructured()
        async def ainvoke(self, prompt, **kwargs):
            return MockRawResponse()

    wrapper = _StructuredWrapper(MemberStatement, temperature=0.5)
    wrapper.llm = MockLLM()
    wrapper.structured = MockStructured()

    async def runner():
        return await wrapper.ainvoke("any prompt")

    result = asyncio.run(runner())
    assert result.reasoning_summary == "fallback 路径"
    assert result.stance == "oppose"
