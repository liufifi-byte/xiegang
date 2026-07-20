"""LLM 调用并发控制。

V1-S2：把 parallel_research + round_debate 的串行 LLM 调用改成 asyncio.gather 并发。

Semaphore 限制对 GLM API 的并发，避免触发 429 / 速率限制。
默认 3 并发（5 议员的两批次调用，刚好不爆款）。
"""
import asyncio

# 全局信号量（进程内共享）：限制同时在飞的 LLM 请求数
_SEMAPHORE: asyncio.Semaphore | None = None


def get_llm_semaphore() -> asyncio.Semaphore:
    """返回 LLM 调用并发信号量（惰性初始化）。"""
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(3)
    return _SEMAPHORE
