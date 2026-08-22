"""Multi-Agent Scheduler（spec 2026-08-22 §16.2，Phase 3）。

并发策略：
- 同一会议默认最多 3 个活跃 Child Agent（spec §16.2），Semaphore 实现；
- 每个任务独立 ExpertAgentRunner（预算、超时、失败隔离）；
- 事件经 asyncio.Queue 汇聚成单一流按到达顺序 yield；
- 取消：request_cancel() 标记全部在途 runner 并 interrupt 当前轮。

公平性：ExpertAgentRunner 内部各自的轮次预算相等；并发上限防止单个
会议吃满全局 Runtime。全局公平（跨会议）由部署侧 Runtime 池承担，
不在本层实现。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator

from app.decision_harness.context_compiler import ContextPacket, ExpertTaskPacket
from app.decision_harness.event_bridge import make_product_event
from app.decision_harness.expert_loop import ExpertAgentRunner, ExpertRunOutcome
from app.decision_harness.repository import DecisionRunRepository
from app.decision_harness.runtime import AgentRuntime

# spec §16.2：同一会议默认最多 3 个活跃 Child Agent
DEFAULT_MAX_CONCURRENCY = 3


@dataclass
class ExpertJob:
    packet: ExpertTaskPacket
    context: ContextPacket


class _OutcomeSentinel:
    """队列内标记：某 runner 已结束（区别于产品事件 dict）。"""

    __slots__ = ("runner",)

    def __init__(self, runner: ExpertAgentRunner):
        self.runner = runner


class ExpertScheduler:
    """并发调度一批专家任务。用法：

        sched = ExpertScheduler(run_id=..., runtime=..., repo=...)
        async for event in sched.run_all(jobs): ...
        outcomes = sched.outcomes  # list[ExpertRunOutcome]，与 jobs 同序
    """

    def __init__(
        self,
        *,
        run_id: str,
        runtime: AgentRuntime,
        repo: DecisionRunRepository,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        child_turn_limit: int = 4,
        timeout_seconds: float = 300.0,
    ):
        self.run_id = run_id
        self.runtime = runtime
        self.repo = repo
        self.max_concurrency = max(1, max_concurrency)
        self.child_turn_limit = child_turn_limit
        self.timeout_seconds = timeout_seconds
        self.outcomes: list[ExpertRunOutcome] = []
        # 观测（测试/运维断言用）
        self.observed_concurrency = 0
        self._live_runners: set[ExpertAgentRunner] = set()
        self._cancel_requested = False

    def request_cancel(self) -> None:
        """取消：在途 runner 停止开新轮并 interrupt 当前轮。幂等。"""
        self._cancel_requested = True
        for runner in list(self._live_runners):
            runner.request_cancel()

    async def cancel(self) -> None:
        self.request_cancel()
        for runner in list(self._live_runners):
            await self.runtime.interrupt_turn(runner.session_id)

    async def run_all(self, jobs: list[ExpertJob]) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        semaphore = asyncio.Semaphore(self.max_concurrency)
        self.outcomes = []
        total = len(jobs)
        finished = 0
        inflight = 0

        async def worker(job: ExpertJob) -> None:
            nonlocal inflight
            async with semaphore:
                if self._cancel_requested:
                    queue.put_nowait(_OutcomeSentinel(None))
                    return
                runner = ExpertAgentRunner(
                    run_id=self.run_id,
                    packet=job.packet,
                    context=job.context,
                    runtime=self.runtime,
                    repo=self.repo,
                    child_turn_limit=self.child_turn_limit,
                    timeout_seconds=self.timeout_seconds,
                )
                self._live_runners.add(runner)
                inflight += 1
                self.observed_concurrency = max(self.observed_concurrency, inflight)
                try:
                    async for event in runner.run():
                        await queue.put(event)
                except Exception as exc:  # 双保险：runner 理论上不抛
                    await queue.put(make_product_event(
                        self.run_id, "agent_failed",
                        {"task_id": job.packet.task_id,
                         "message": f"scheduler_worker_error:{type(exc).__name__}"},
                    ))
                finally:
                    self._live_runners.discard(runner)
                    inflight -= 1
                    await queue.put(_OutcomeSentinel(runner))

        tasks = [asyncio.create_task(worker(job)) for job in jobs]
        try:
            while finished < total:
                item = await queue.get()
                if isinstance(item, _OutcomeSentinel):
                    finished += 1
                    if item.runner is not None and item.runner.outcome is not None:
                        self.outcomes.append(item.runner.outcome)
                    continue
                yield item
        finally:
            if finished < total:
                # 消费方提前退出（异常/取消）：停掉全部 worker 再返回
                self.request_cancel()
                for runner in list(self._live_runners):
                    try:
                        await self.runtime.interrupt_turn(runner.session_id)
                    except Exception:
                        pass
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                await asyncio.gather(*tasks, return_exceptions=True)
