"""V4 迁移基线评测（spec §20 Phase 0）。

从 Supabase 拉取最近 N 场已终结议会（done/failed/cancelled），
离线计算稳定性、完成率、引用、重复率、延迟等基线指标并落盘 JSON。

可重复性：查询确定性排序（created_at desc + id），报告记录快照时间上界；
不发起任何 LLM 调用。shadow/codex_harness 阶段用同一脚本对照。

用法（backend 目录）:
    ./.venv/bin/python -m evals.decision_baseline --limit 25
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1)))))
    return ordered[k]


def _repetition_rate(texts: list[str], n: int = 8) -> float | None:
    """专家研究结论间的字符 n-gram 平均 Jaccard 相似度（重复率代理指标）。

    只取分析主体段（【】标题行之后的内容），避免标题模板抬高重复度。
    """

    def grams(text: str) -> set[str]:
        cleaned = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("【")
        )
        return {cleaned[i : i + n] for i in range(max(0, len(cleaned) - n + 1))}

    valid = [t for t in texts if t and len(t) >= n]
    if len(valid) < 2:
        return None
    scores = []
    for i in range(len(valid)):
        for j in range(i + 1, len(valid)):
            a, b = grams(valid[i]), grams(valid[j])
            union = a | b
            if union:
                scores.append(len(a & b) / len(union))
    return statistics.mean(scores) if scores else None


def _analysis_body(content: str) -> str:
    return content or ""


def collect_cases(limit: int) -> list[dict]:
    from app.db import get_supabase

    result = (
        get_supabase()
        .table("parliaments")
        .select(
            "id,status,created_at,finished_at,total_duration_seconds,"
            "selected_expert_ids,transcript,members_state,summary"
        )
        .in_("status", ["done", "failed", "cancelled"])
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(result.data or [])


def compute_metrics(cases: list[dict]) -> dict:
    total = len(cases)
    by_status: dict[str, int] = {}
    for c in cases:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1

    done = [c for c in cases if c["status"] == "done"]
    durations = [
        float(c["total_duration_seconds"])
        for c in done
        if c.get("total_duration_seconds")
    ]

    experts_counts, research_rates, degradation_rates, stance_diversity = [], [], [], []
    repetition_scores, citation_stats = [], {"evidence": 0, "with_url": 0}

    for c in cases:
        transcript = c.get("transcript") or []
        selected = c.get("selected_expert_ids") or []
        research = [e for e in transcript if e.get("type") == "research"]
        debate = [e for e in transcript if e.get("type") == "debate"]
        degraded = [e for e in transcript if e.get("type") == "debate_degraded"]

        if selected:
            experts_counts.append(len(selected))
            research_rates.append(len(research) / len(selected))
        if debate or degraded:
            degradation_rates.append(
                len(degraded) / max(1, len(debate) + len(degraded))
            )
        stances = {
            (v or {}).get("stance")
            for v in (c.get("members_state") or {}).values()
            if (v or {}).get("stance")
        }
        if stances:
            stance_diversity.append(len(stances))

        rep = _repetition_rate([_analysis_body(e.get("content") or "") for e in research])
        if rep is not None:
            repetition_scores.append(rep)

        for e in research:
            for ev in e.get("evidence") or []:
                citation_stats["evidence"] += 1
                if ev.get("source_url"):
                    citation_stats["with_url"] += 1

    return {
        "total_cases": total,
        "by_status": by_status,
        "completion_rate": by_status.get("done", 0) / total if total else None,
        "cancel_rate": by_status.get("cancelled", 0) / total if total else None,
        "failure_rate": by_status.get("failed", 0) / total if total else None,
        "duration_seconds": {
            "p50": _percentile(durations, 50),
            "p95": _percentile(durations, 95),
            "max": max(durations) if durations else None,
            "mean": statistics.mean(durations) if durations else None,
        },
        "experts_per_council": {
            "mean": statistics.mean(experts_counts) if experts_counts else None,
            "min": min(experts_counts) if experts_counts else None,
            "max": max(experts_counts) if experts_counts else None,
        },
        "research_publish_rate": {
            "mean": statistics.mean(research_rates) if research_rates else None,
        },
        "debate_degradation_rate": {
            "mean": statistics.mean(degradation_rates) if degradation_rates else None,
            "councils_with_degradation": sum(1 for r in degradation_rates if r > 0),
        },
        "stance_diversity_per_council": {
            "mean": statistics.mean(stance_diversity) if stance_diversity else None,
        },
        "repetition_rate": {
            "mean": statistics.mean(repetition_scores) if repetition_scores else None,
        },
        "citation": {
            "evidence_items": citation_stats["evidence"],
            "url_coverage": (
                citation_stats["with_url"] / citation_stats["evidence"]
                if citation_stats["evidence"]
                else None
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="V4 迁移基线报告")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument(
        "--out-dir", type=str, default=str(Path(__file__).parent / "baselines")
    )
    args = parser.parse_args()

    cases = collect_cases(args.limit)
    if not cases:
        print("没有可用的已终结议会，基线为空。")
        return 1

    metrics = compute_metrics(cases)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_upper_bound": max(c["created_at"] for c in cases),
        "limit": args.limit,
        "runtime_mode": "legacy",
        "metrics": metrics,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"baseline-{datetime.now(timezone.utc).date().isoformat()}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    m = metrics
    print(f"基线已写入 {out_path}\n")
    print(f"样本: {m['total_cases']} 场（{m['by_status']}）")
    print(f"完成率: {m['completion_rate']:.2%}")
    print(f"耗时 p50/p95: {m['duration_seconds']['p50']}s / {m['duration_seconds']['p95']}s")
    print(f"研究发布率: {m['research_publish_rate']['mean']:.2%}")
    print(f"辩论降级率: {m['debate_degradation_rate']['mean']:.2%}")
    print(f"重复率(n-gram): {m['repetition_rate']['mean']:.3f}")
    print(f"证据 URL 覆盖率: {m['citation']['url_coverage']:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
