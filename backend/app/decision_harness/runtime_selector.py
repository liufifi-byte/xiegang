"""Runtime 选择器：legacy / shadow / codex_harness + 灰度（spec §19.4，Phase 8）。

规则：
- DECISION_RUNTIME_MODE=codex_harness → 全量新 Runtime；
- =shadow → legacy 仍是用户侧输出，harness 全量后台对照；
- =legacy（默认）→ 看灰度比例 decision_runtime_canary_percent：
  按 parliament_id 确定性哈希选前 N% 走 codex_harness，其余 legacy。

回滚（§20 Phase 8 任务 6）：把 canary 调回 0 或 mode 改回 legacy，
无需改代码——两个环境变量即一键回滚。
"""
from __future__ import annotations

import hashlib

from app.config import get_settings
from app.decision_harness.runtime import (
    AgentRuntimeError,
    RuntimeMode,
    get_runtime_mode,
)


def _hash_percent(key: str) -> int:
    """确定性 0-99：同一议会永远落同一桶（重跑不漂移）。"""
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def select_runtime_mode(parliament_id: str) -> RuntimeMode:
    mode = get_runtime_mode()
    if mode in ("codex_harness", "shadow"):
        return mode
    # legacy + 灰度
    canary = get_settings().decision_runtime_canary_percent
    if not 0 <= canary <= 100:
        raise AgentRuntimeError(
            "invalid_canary_percent",
            f"DECISION_RUNTIME_CANARY_PERCENT={canary} 非法（0-100）",
        )
    if canary > 0 and _hash_percent(parliament_id) < canary:
        return "codex_harness"
    return "legacy"


def is_shadow_enabled(parliament_id: str) -> bool:
    return select_runtime_mode(parliament_id) == "shadow"
