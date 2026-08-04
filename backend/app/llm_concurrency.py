"""LLM 调用并发控制。

V1-S2：把 parallel_research + round_debate 的串行 LLM 调用改成 asyncio.gather 并发。

Semaphore 限制对 GLM API 的并发，避免触发 429 / 速率限制。

并发上限从硬编码改为环境变量配置——遵报告 P0 原则"不要直接调大并发"。
默认仍为 3（与历史行为一致，不冲垮账户额度）。需要放宽时通过
LLM_MAX_CONCURRENCY 环境变量显式设置，且需先确认智谱账户额度足够支撑。

注意：此限制只在单 Python 进程内生效。多 Worker 部署（Railway / Fly 副本）
时，实际总并发 = worker 数 × 该值。需要跨进程全局限流，应走 P2 的 Redis Gateway。

============================================================
公平分槽（防止一个议会独占所有 LLM 槽位）：
- 全局 semaphore：跨议会共享，限制进程内总并发
- 每议会 semaphore：限制单议会内同时飞的 LLM 请求数
- acquire_llm_slots(parliament_id)：嵌套上下文——先全局后议会
  调用顺序对所有节点保持一致，避免 lock-ordering 死锁
============================================================
"""
import asyncio
import os
from contextlib import asynccontextmanager

from app.config import get_settings

# 全局信号量（进程内共享）：限制同时在飞的 LLM 请求数
_SEMAPHORE: asyncio.Semaphore | None = None

# 默认并发上限。保持 3，与历史一致。
# 调高前须知：必须先确认智谱账户并发额度（1302 = 账户并发上限；
# 1305 = 平台过载）。多 Worker 部署时实际总并发会乘以 worker 副本数。
_DEFAULT_MAX_CONCURRENCY = 3


def _resolve_max_concurrency() -> int:
    """从环境变量读取并发上限，带防御性校验。"""
    raw = os.environ.get("LLM_MAX_CONCURRENCY", "").strip()
    if not raw:
        return _DEFAULT_MAX_CONCURRENCY
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_CONCURRENCY
    # 至少 1、最多 50（合理上限，避免误配大数把账户打挂）
    return max(1, min(value, 50))


def get_llm_semaphore() -> asyncio.Semaphore:
    """返回 LLM 调用并发信号量（惰性初始化）。

    配置来源：环境变量 LLM_MAX_CONCURRENCY，默认 3。
    """
    global _SEMAPHORE
    if _SEMAPHORE is None:
        limit = _resolve_max_concurrency()
        _SEMAPHORE = asyncio.Semaphore(limit)
    return _SEMAPHORE


# ============================================================
# 每议会 semaphore 缓存：parliament_id → Semaphore
# ============================================================
_PER_PARLIAMENT_SEMAPHORES: dict[str, asyncio.Semaphore] = {}


def _resolve_per_parliament_concurrency() -> int:
    """从配置读取每议会并发上限。默认 2。"""
    try:
        return max(1, get_settings().per_parliament_llm_concurrency)
    except Exception:
        return 2


def get_per_parliament_semaphore(parliament_id: str) -> asyncio.Semaphore:
    """返回某议会的本地 semaphore（惰性初始化 + 缓存）。

    议会结束时调用 clear_per_parliament_semaphore 释放缓存。
    """
    sem = _PER_PARLIAMENT_SEMAPHORES.get(parliament_id)
    if sem is None:
        sem = asyncio.Semaphore(_resolve_per_parliament_concurrency())
        _PER_PARLIAMENT_SEMAPHORES[parliament_id] = sem
    return sem


def clear_per_parliament_semaphore(parliament_id: str) -> None:
    """议会结束（archive / failed）时清理缓存，避免内存累积。"""
    _PER_PARLIAMENT_SEMAPHORES.pop(parliament_id, None)


@asynccontextmanager
async def acquire_llm_slots(parliament_id: str | None):
    """公平分槽：嵌套拿"全局 semaphore → 每议会 semaphore"。

    lock-ordering 必须一致：所有调用者都先全局后议会，避免死锁。
    parliament_id=None 时（如无具体议会上下文）只拿全局 semaphore。
    """
    global_sem = get_llm_semaphore()
    async with global_sem:
        if parliament_id:
            pp_sem = get_per_parliament_semaphore(parliament_id)
            async with pp_sem:
                yield
        else:
            yield


def reset_semaphore_for_tests() -> None:
    """测试辅助：强制重置信号量，使下一次 get_llm_semaphore 重新读环境变量。"""
    global _SEMAPHORE
    _SEMAPHORE = None
    _PER_PARLIAMENT_SEMAPHORES.clear()
