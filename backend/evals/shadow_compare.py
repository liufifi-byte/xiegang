"""Shadow 对比评测与盲评导出（spec §20 Phase 8 任务 2/3）。

- build_comparison：legacy 会议行 vs shadow decision_run 的结构化对比
  （完成度、时长、报告强度、不确定清单、立场分布、证据量）；
- aggregate：多场对比的汇总指标（灰度门槛的数字依据）；
- render_blind_pair：匿名 A/B 盲评稿（标签随机、可复现，供人工评分）。

纯函数（输入行字典），不直接连库——调用方负责取数，便于离线测试。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def build_comparison(
    *,
    parliament_id: str,
    legacy: dict[str, Any] | None,
    shadow_row: dict[str, Any] | None,
    shadow_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """单场 legacy vs shadow 对比。legacy 行来自 parliaments 表投影。"""
    legacy_done = bool(legacy and legacy.get("status") == "done")
    state = (shadow_row or {}).get("decision_state") or {}
    report = state.get("final_report") or {}
    rec = report.get("recommendation") or {}
    findings = report.get("expert_findings") or []

    stances = [f.get("stance") for f in findings if f.get("stance")]
    return {
        "parliament_id": parliament_id,
        "legacy": {
            "status": (legacy or {}).get("status"),
            "completed": legacy_done,
            "duration_seconds": (legacy or {}).get("total_duration_seconds"),
            "has_summary": bool((legacy or {}).get("summary")),
        },
        "shadow": {
            "status": (shadow_row or {}).get("status"),
            "exit_status": (shadow_row or {}).get("exit_status"),
            "has_final_report": bool(report),
            "recommendation_strength": rec.get("strength"),
            "remaining_uncertainties": len(report.get("remaining_uncertainties") or []),
            "reversal_conditions": len(report.get("reversal_conditions") or []),
            "stance_diversity": len(set(stances)),
            "expert_count": len(findings),
            "evidence_count": len(shadow_evidence or []),
        },
        "both_completed": legacy_done and bool(report),
    }


def aggregate(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总指标：灰度决策依据（spec Phase 8 任务 5 的门槛数字）。"""
    n = len(comparisons)
    if not n:
        return {"runs": 0}
    both = sum(1 for c in comparisons if c["both_completed"])
    strengths: list[str] = [
        c["shadow"]["recommendation_strength"]
        for c in comparisons if c["shadow"]["recommendation_strength"]
    ]
    return {
        "runs": n,
        "both_completed": both,
        "both_completed_rate": round(both / n, 3),
        "shadow_report_rate": round(
            sum(1 for c in comparisons if c["shadow"]["has_final_report"]) / n, 3),
        "legacy_completed_rate": round(
            sum(1 for c in comparisons if c["legacy"]["completed"]) / n, 3),
        "shadow_avg_uncertainties": round(
            sum(c["shadow"]["remaining_uncertainties"] for c in comparisons) / n, 2),
        "strength_distribution": {
            s: strengths.count(s) for s in set(strengths)
        },
    }


def render_blind_pair(
    *,
    legacy_summary: dict[str, Any] | None,
    harness_report: dict[str, Any] | None,
    seed: str = "default",
) -> str:
    """匿名 A/B 盲评稿：标签 X/Y 由种子决定（同种子可复现，便于复核）。"""
    order = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8], 16) % 2
    a_label, b_label = ("X", "Y") if order == 0 else ("Y", "X")

    def _legacy_text(s: dict[str, Any] | None) -> str:
        if not s:
            return "（该场未产出总结）"
        rec = s.get("recommendation") or s.get("final_recommendation") or {}
        parts = [f"结论方向：{rec.get('option') or s.get('winner') or '未明确'}"]
        if s.get("summary_text"):
            parts.append(f"要点：{str(s['summary_text'])[:600]}")
        return "\n".join(parts)

    def _harness_text(r: dict[str, Any] | None) -> str:
        if not r:
            return "（该场未产出报告）"
        rec = r.get("recommendation") or {}
        lines = [
            f"结论方向：{rec.get('option') or '未明确'}"
            f"（强度：{rec.get('strength') or '未知'}）",
            f"摘要：{(r.get('summary') or rec.get('summary') or '')}"[:600],
            f"待确认事项：{'；'.join(r.get('remaining_uncertainties') or []) or '无'}",
            f"反转条件：{'；'.join(r.get('reversal_conditions') or []) or '无'}",
        ]
        return "\n".join(lines)

    pairs = {
        a_label: _legacy_text(legacy_summary),
        b_label: _harness_text(harness_report),
    }
    return (
        "# 盲评对比（A/B 匿名）\n\n"
        "两份结论来自不同系统，请独立打分（1-5：清晰度/依据充分度/可执行性）。\n"
        f"种子：{seed}\n\n"
        f"## 方案 {a_label}\n\n{pairs[a_label]}\n\n"
        f"## 方案 {b_label}\n\n{pairs[b_label]}\n\n"
        "## 你的评分\n\n"
        "| 维度 | 方案 X | 方案 Y |\n|---|---|---|\n"
        "| 清晰度 | | |\n| 依据充分度 | | |\n| 可执行性 | | |\n"
    )


def dump_comparison_json(comparisons: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"aggregate": aggregate(comparisons), "comparisons": comparisons},
        ensure_ascii=False, indent=2,
    )
