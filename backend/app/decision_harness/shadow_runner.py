"""Shadow Runner（spec §19.4 / §20 Phase 8）。

模式 shadow 时：legacy LangGraph 照常服务用户，本模块在后台把同一场
议会跑一遍 Decision Harness，结果只进 decision_runs（不写
parliament_events——用户绝不能在旧界面看到影子事件）。

铁律：
- 用户侧行为零变化（不碰 parliaments / parliament_events / SSE）；
- 影子任务永不抛异常：任何失败记 decision_run 状态，不影响主流程；
- Runtime 不可用（SDK/二进制缺失）→ shadow_skipped，静默记录原因。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from app.config import get_settings

logger = logging.getLogger("xiegang.shadow")


def _sdk_available() -> bool:
    try:
        import openai_codex  # noqa: F401
        return True
    except ImportError:
        return False


def _build_runtime():
    """构造真实 Codex Runtime（指向自建二进制或 SDK 自带）。"""
    from app.decision_harness.codex_runtime import CodexAgentRuntime

    return CodexAgentRuntime()


def _decision_from_task(task: dict[str, Any]):
    from app.decision_harness.schemas import DecisionModel

    topic = str(task.get("topic") or "").strip()
    background = str(task.get("user_background") or "").strip()
    if len(topic) < 4:
        topic = f"用户决策：{topic or '未命名议题'}"
    constraints = [background[:100]] if background else []
    return DecisionModel(
        question=topic[:500],
        options=[],
        constraints=constraints,
    )


async def run_shadow(
    parliament_id: str,
    task: dict[str, Any],
    *,
    runtime: Any | None = None,
    run_id: str | None = None,
) -> str:
    """跑一场影子决策（全链：计划→专家→辩论→终局）。

    返回 decision run id。任何异常都被收敛为 run 状态，绝不上抛。
    runtime 参数仅供测试注入（Fake）；生产走真实 CodexAgentRuntime。
    """
    from app.decision_harness.repository import DecisionRunRepository
    from app.decision_harness.runtime import AgentRuntimeError
    from app.decision_harness.schemas import BudgetPolicy
    from app.decision_harness.workflow import DecisionWorkflow

    shadow_run_id = run_id or str(uuid.uuid4())  # DB 主键为 UUID
    repo = DecisionRunRepository()
    decision = _decision_from_task(task)

    async def _mark(status: str, error: str | None = None) -> None:
        try:
            await repo.update_run(shadow_run_id, status=status, error=error)
        except Exception:
            pass

    await repo.create_run(
        shadow_run_id,
        parliament_id=parliament_id,
        runtime_mode="shadow",
        decision_state=decision.model_dump(mode="json"),
        budget=BudgetPolicy().model_dump(mode="json"),
    )
    await repo.update_run(shadow_run_id, status="shadow_running")

    if runtime is None:
        if not _sdk_available():
            await _mark("shadow_skipped", "codex_sdk_unavailable")
            return shadow_run_id
        try:
            runtime = _build_runtime()
        except (AgentRuntimeError, Exception) as exc:
            await _mark("shadow_skipped", f"codex_runtime_unavailable:{type(exc).__name__}")
            return shadow_run_id

    wf = DecisionWorkflow(
        run_id=shadow_run_id,
        decision=decision,
        budget=BudgetPolicy(),
        runtime=runtime,
        repo=repo,
        parliament_id=parliament_id,
        runtime_mode="shadow",
        pre_created=True,
        auto_continue_without_user=True,
    )
    try:
        # 影子事件就地丢弃（§15.1 翻译层验证已由测试覆盖；用户不消费影子流）
        async for _ in wf.run():
            pass
        if wf.outcome and wf.outcome.plan is not None:
            # Phase 4 研究阶段：claim 驱动真搜索 → Evidence Kernel 闸门
            # （自媒体→线索不立据；过期→拒绝；专家只见可用证据摘要）
            from app.decision_harness.research_lead import LegacyWebSearchProvider

            async for _ in wf.run_research(provider=LegacyWebSearchProvider()):
                pass
            async for _ in wf.run_experts():
                pass
            async for _ in wf.run_debate():
                pass
        async for _ in wf.run_finalize():
            pass
        await repo.update_run(shadow_run_id, status="shadow_completed")
    except Exception as exc:  # 影子失败不影响 legacy 主流程
        logger.warning("shadow run %s 失败: %s", shadow_run_id, exc)
        await _mark("shadow_failed", f"{type(exc).__name__}:{str(exc)[:300]}")
    finally:
        closer = getattr(runtime, "aclose", None)
        if callable(closer):
            try:
                await closer()
            except Exception:
                pass
    return shadow_run_id


def launch_shadow_if_enabled(parliament_id: str, task: dict[str, Any]) -> str | None:
    """worker 挂点：shadow 模式才点火，fire-and-forget，绝不抛异常。"""
    try:
        from app.decision_harness.runtime_selector import is_shadow_enabled

        if not is_shadow_enabled(parliament_id):
            return None
        loop = asyncio.get_running_loop()
        loop.create_task(run_shadow(parliament_id, task))
        return "launched"
    except Exception as exc:  # pragma: no cover - 防御
        logger.warning("shadow 启动失败（忽略）: %s", exc)
        return None
