"""V2-S2 Cancel Guard：让 ainvoke 能被硬中断。

工作原理：
1. 每次 ainvoke 调用前，把当前 task 登记到 inflight 注册表
2. 用 asyncio.wait_for(timeout) 包裹，超时 / cancel 立即触发 CancelledError
3. request_cancel() 时遍历该议会所有 inflight task → task.cancel()
4. 当前 LLM SDK (httpx) 接到 CancelledError 后能立即断开连接

LangChain ChatOpenAI 1.3+ 完全支持 await cancel 同时中断 HTTP 请求。
"""
import asyncio
from collections import defaultdict

# inflight 注册表：parliament_id → set[asyncio.Task]
# 用 defaultdict 避免检查 key 存在性
_INFLIGHT: dict[str, set[asyncio.Task]] = defaultdict(set)
_LOCK = asyncio.Lock()


async def register_inflight(parliament_id: str, task: asyncio.Task) -> None:
    async with _LOCK:
        _INFLIGHT[parliament_id].add(task)


async def unregister_inflight(parliament_id: str, task: asyncio.Task) -> None:
    async with _LOCK:
        _INFLIGHT[parliament_id].discard(task)
        if not _INFLIGHT[parliament_id]:
            _INFLIGHT.pop(parliament_id, None)


async def cancel_all_inflight(parliament_id: str) -> int:
    """取消该议会所有正在飞的 LLM 调用。返回取消的 task 数。"""
    async with _LOCK:
        tasks = list(_INFLIGHT.get(parliament_id, set()))
    cancelled = 0
    for t in tasks:
        if not t.done():
            t.cancel()
            cancelled += 1
    return cancelled


def get_inflight_count(parliament_id: str) -> int:
    return len(_INFLIGHT.get(parliament_id, set()))


async def guard_ainvoke(
    parliament_id: str,
    coro,
    timeout: float = 120,
):
    """包裹一个 LLM 调用，支持硬中断 + 超时。

    Args:
        parliament_id: 用于 cancel 关联
        coro: 已构造的 coroutine（awaitable），通常是 structured.ainvoke(prompt)
        timeout: 超时秒数，触发 asyncio.TimeoutError

    Returns:
        LLM 返回的结果

    Raises:
        asyncio.TimeoutError: 超时
        asyncio.CancelledError: 被 cancel_all_inflight 取消
    """
    # 把 coroutine 包成 Task，方便 cancel
    task = asyncio.ensure_future(coro)
    await register_inflight(parliament_id, task)

    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        task.cancel()
        raise
    except asyncio.CancelledError:
        # 外部 cancel 传过来（request_cancel）
        task.cancel()
        raise
    finally:
        await unregister_inflight(parliament_id, task)


def _clear_all_for_tests() -> None:
    """测试辅助：清空所有 inflight 注册。"""
    _INFLIGHT.clear()
