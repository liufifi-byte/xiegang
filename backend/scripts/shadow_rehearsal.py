"""Shadow 彩排（spec §20 Phase 8 任务 1/2）：真实历史议题 × 新系统 × 智谱真跑。

用法：
    ./.venv/bin/python scripts/shadow_rehearsal.py [--cases N] [--port 8095]

流程：
1. 进程内启动 GLM→Responses 网关（线程）；
2. 从 Supabase 取最近 N 个已完成议会的真实议题（只读）；
3. 每场跑 run_shadow 全链（计划→专家→辩论→终局，模型走智谱），
   结果写 decision_runs（不碰 parliaments / parliament_events）；
4. legacy vs shadow 自动对比（evals.shadow_compare），
   报告存 backend/evals/baselines/shadow-rehearsal-<date>.json。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("shadow_rehearsal")
logger.setLevel(logging.INFO)

CODEX_BIN = str(Path.home() / "Desktop/codex-fork/codex-rs/target/debug/codex-app-server")


def start_gateway(port: int):
    import uvicorn
    from app.glm_gateway import app as gateway_app

    server = uvicorn.Server(uvicorn.Config(
        gateway_app.app, host="127.0.0.1", port=port, log_level="error",
    ))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("GLM 网关启动失败")


def fetch_recent_parliaments(limit: int) -> list[dict]:
    from app.db import direct_db_url
    import psycopg

    with psycopg.connect(direct_db_url(), connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, topic, topic_category, user_background, status,
                       total_duration_seconds, summary
                FROM parliaments
                WHERE status = 'done' AND topic IS NOT NULL AND length(topic) >= 8
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
    cols = ["id", "topic", "topic_category", "user_background", "status",
            "total_duration_seconds", "summary"]
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        d["id"] = str(d["id"])
        if d["summary"] is not None and not isinstance(d["summary"], dict):
            try:
                d["summary"] = json.loads(d["summary"])
            except Exception:
                d["summary"] = {"raw": str(d["summary"])[:200]}
        out.append(d)
    return out


def fetch_legacy_row(parliament_id: str) -> dict | None:
    from app.db import direct_db_url
    import psycopg

    with psycopg.connect(direct_db_url(), connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, total_duration_seconds, summary FROM parliaments WHERE id = %s",
                (parliament_id,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    summary = row[2]
    if summary is not None and not isinstance(summary, dict):
        try:
            summary = json.loads(summary)
        except Exception:
            summary = None
    return {"status": row[0], "total_duration_seconds": row[1], "summary": summary}


async def run_case(p: dict, port: int) -> dict:
    from app.config import get_settings
    from app.decision_harness.repository import DecisionRunRepository
    from app.decision_harness.shadow_runner import run_shadow
    from evals.shadow_compare import build_comparison

    settings = get_settings()
    settings.codex_use_glm_gateway = True
    settings.glm_gateway_url = f"http://127.0.0.1:{port}"
    settings.codex_bin = CODEX_BIN

    task = {
        "topic": p["topic"],
        "topic_category": p.get("topic_category"),
        "user_background": p.get("user_background"),
    }
    logger.info("开始影子运行：%s topic=%r", p["id"], p["topic"][:60])
    started = time.monotonic()
    shadow_run_id = await run_shadow(p["id"], task)
    elapsed = round(time.monotonic() - started, 1)

    repo = DecisionRunRepository()
    shadow_row = await repo.get_run(shadow_run_id)
    evidence = await repo.list_evidence(shadow_run_id)
    comparison = build_comparison(
        parliament_id=p["id"],
        legacy=fetch_legacy_row(p["id"]),
        shadow_row=shadow_row,
        shadow_evidence=evidence,
    )
    comparison["shadow_run_id"] = shadow_run_id
    comparison["shadow_duration_seconds"] = elapsed
    return comparison


async def main_async(cases: int, port: int) -> None:
    gateway = start_gateway(port)
    logger.info("GLM 网关就绪 @ 127.0.0.1:%d", port)

    parliaments = fetch_recent_parliaments(cases)
    if not parliaments:
        logger.warning("没有可用的历史议题（需要 status=done 且 topic≥8 字）")
        return
    logger.info("取到 %d 个真实历史议题", len(parliaments))

    comparisons = []
    for p in parliaments:
        try:
            c = await run_case(p, port)
        except Exception as exc:  # 单场失败不中断彩排
            logger.warning("场次 %s 彩排异常: %s", p["id"], exc)
            c = {"parliament_id": p["id"], "rehearsal_error": str(exc)[:200]}
        comparisons.append(c)
        logger.info("完成：%s", json.dumps(c, ensure_ascii=False)[:300])

    from evals.shadow_compare import aggregate, dump_comparison_json

    report = {"aggregate": aggregate(comparisons), "comparisons": comparisons}
    out = Path(__file__).resolve().parents[1] / "evals" / "baselines" / (
        f"shadow-rehearsal-{date.today().isoformat()}.json"
    )
    out.write_text(dump_comparison_json(comparisons), encoding="utf-8")
    logger.info("报告已写入 %s", out)
    logger.info("汇总：%s", json.dumps(report["aggregate"], ensure_ascii=False))
    gateway.should_exit = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=2)
    parser.add_argument("--port", type=int, default=8095)
    args = parser.parse_args()
    asyncio.run(main_async(args.cases, args.port))
