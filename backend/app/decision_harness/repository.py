"""Decision Harness 持久化仓库（spec 2026-08-22 §14，Phase 1）。

表：decision_runs / agent_runs / plan_versions / evidence_records / debate_issues
（migration 002，幂等）。事件不在此处——继续走 parliament_events（spec §14.6）。

两种模式（同接口，模式选择与 app/runtime_context.py 一致）：
- DB 模式：psycopg 直连（direct_db_url，绕过 Supabase pooler），asyncio.to_thread 包同步调用；
- 内存模式：SKIP_DB_CONTEXT=true 或 force_memory=True 时启用；
  存储是模块级单例，同一进程内多个仓库实例共享——用于离线测试
  "worker 重启后计划仍在"（跨实例重建仓库模拟重启，见 test_repository.py）。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg

from app.db import direct_db_url

# 允许 update_run / create_run 写入的列（防 SQL 注入面收敛到白名单）
_RUN_COLUMNS = {
    "parliament_id",
    "runtime_mode",
    "root_thread_id",
    "workflow_phase",
    "status",
    "state_version",
    "decision_state",
    "active_plan",
    "budget",
    "usage",
    "finalization_state",
    "error",
    "exit_status",
    "finished_at",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


def _db_id(key: str) -> str:
    """逻辑 ID → 数据库 UUID（spec §14 主键为 UUID）。

    本来就是合法 UUID 的原样规范化（保持大小写/连字符一致）；
    复合逻辑 ID（如 "run:agent:task"）用 uuid5 确定性映射——
    同一逻辑 ID 永远同一个 UUID，upsert 幂等。内存模式不经此转换。
    """
    try:
        return str(uuid.UUID(str(key)))
    except (ValueError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"shenmo:{key}"))


# ============ 模块级内存存储（测试 / SKIP_DB 模式）============

_MEMORY: dict[str, dict[Any, Any]] = {
    "runs": {},  # run_id -> row dict
    "agent_runs": {},  # agent_run_id -> row dict
    "plan_versions": {},  # (run_id, version) -> row dict
    "evidence": {},  # (run_id, evidence_id) -> row dict
    "disputes": {},  # (run_id, dispute_key) -> row dict
}


def _clear_memory_for_tests() -> None:
    """仅供测试调用。"""
    for section in _MEMORY.values():
        section.clear()


class DecisionRunRepository:
    """decision_runs 族表的读写门面。"""

    def __init__(self, *, force_memory: bool = False):
        self._memory = force_memory or os.environ.get(
            "SKIP_DB_CONTEXT", ""
        ).lower() in ("1", "true", "yes")

    # ============ run 行 ============

    async def create_run(
        self,
        run_id: str,
        *,
        parliament_id: str | None = None,
        runtime_mode: str,
        decision_state: dict[str, Any],
        budget: dict[str, Any],
    ) -> bool:
        """创建 run 行。已存在返回 False（幂等，用于重启恢复路径）。"""
        if self._memory:
            if run_id in _MEMORY["runs"]:
                return False
            _MEMORY["runs"][run_id] = {
                "id": run_id,
                "parliament_id": parliament_id,
                "runtime_mode": runtime_mode,
                "root_thread_id": None,
                "workflow_phase": "INTAKE",
                "status": "running",
                "state_version": 1,
                "decision_state": dict(decision_state),
                "active_plan": None,
                "budget": dict(budget),
                "usage": {},
                "finalization_state": None,
                "created_at": _now(),
                "updated_at": _now(),
                "finished_at": None,
            }
            return True
        sql = """
            INSERT INTO decision_runs
              (id, parliament_id, runtime_mode, workflow_phase, status,
               decision_state, budget, created_at, updated_at)
            VALUES (%s, %s, %s, 'INTAKE', 'running', %s::jsonb, %s::jsonb, now(), now())
            ON CONFLICT (id) DO NOTHING
        """
        return await asyncio.to_thread(
            self._exec,
            sql,
            [_db_id(run_id), parliament_id, runtime_mode,
             json.dumps(decision_state), json.dumps(budget)],
            want_rowcount=True,
        )

    async def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        unknown = set(fields) - _RUN_COLUMNS
        if unknown:
            raise ValueError(f"decision_runs 不允许写入列: {sorted(unknown)}")
        if self._memory:
            row = _MEMORY["runs"].get(run_id)
            if row is None:
                raise KeyError(f"decision_run {run_id} 不存在")
            row.update(fields)
            row["updated_at"] = _now()
            return
        assignments = ", ".join(f"{c} = %s" for c in fields)
        sql = f"""
            UPDATE decision_runs SET {assignments}, updated_at = now()
            WHERE id = %s
        """
        await asyncio.to_thread(
            self._exec, sql, [*fields.values(), _db_id(run_id)]
        )

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        if self._memory:
            row = _MEMORY["runs"].get(run_id)
            return dict(row) if row else None
        sql = "SELECT * FROM decision_runs WHERE id = %s"
        return await asyncio.to_thread(self._fetch_one, sql, [_db_id(run_id)])

    async def get_run_by_parliament(self, parliament_id: str) -> dict[str, Any] | None:
        if self._memory:
            for row in _MEMORY["runs"].values():
                if row.get("parliament_id") == parliament_id:
                    return dict(row)
            return None
        sql = "SELECT * FROM decision_runs WHERE parliament_id = %s"
        return await asyncio.to_thread(self._fetch_one, sql, [parliament_id])

    # ============ plan_versions ============

    async def append_plan_version(
        self,
        run_id: str,
        version: int,
        plan: dict[str, Any],
        reason: str | None,
        *,
        created_by_agent_run_id: str | None = None,
    ) -> str:
        """追加计划版本。同版本已存在时覆盖（重放幂等），返回行 id。"""
        row_id = _uuid()
        if self._memory:
            key = (run_id, version)
            existing = _MEMORY["plan_versions"].get(key)
            if existing is not None:
                existing["plan"] = dict(plan)
                existing["reason"] = reason
                return existing["id"]
            _MEMORY["plan_versions"][key] = {
                "id": row_id,
                "decision_run_id": run_id,
                "version": version,
                "plan": dict(plan),
                "reason": reason,
                "created_by_agent_run_id": created_by_agent_run_id,
                "created_at": _now(),
            }
            return row_id
        sql = """
            INSERT INTO plan_versions
              (id, decision_run_id, version, plan, reason, created_by_agent_run_id)
            VALUES (%s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (decision_run_id, version) DO UPDATE
              SET plan = EXCLUDED.plan, reason = EXCLUDED.reason
        """
        await asyncio.to_thread(
            self._exec,
            sql,
            [row_id, _db_id(run_id), version, json.dumps(plan), reason,
             _db_id(created_by_agent_run_id) if created_by_agent_run_id else None],
        )
        return row_id

    async def list_plan_versions(self, run_id: str) -> list[dict[str, Any]]:
        if self._memory:
            rows = [
                dict(v)
                for (rid, _), v in sorted(
                    _MEMORY["plan_versions"].items(), key=lambda kv: kv[0][1]
                )
                if rid == run_id
            ]
            return rows
        sql = """
            SELECT id, decision_run_id, version, plan, reason,
                   created_by_agent_run_id, created_at
            FROM plan_versions WHERE decision_run_id = %s ORDER BY version ASC
        """
        return await asyncio.to_thread(self._fetch_all, sql, [_db_id(run_id)])

    # ============ agent_runs ============

    async def upsert_agent_run(
        self,
        agent_run_id: str,
        *,
        decision_run_id: str,
        agent_type: str,
        expert_id: str | None = None,
        runtime_provider: str | None = None,
        runtime_thread_id: str | None = None,
        task_id: str | None = None,
        status: str = "pending",
        task_packet: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        now = _now()
        if self._memory:
            row = _MEMORY["agent_runs"].get(agent_run_id, {
                "id": agent_run_id,
                "decision_run_id": decision_run_id,
                "agent_type": agent_type,
                "expert_id": expert_id,
                "runtime_provider": runtime_provider,
                "runtime_thread_id": runtime_thread_id,
                "task_id": task_id,
                "status": status,
                "task_packet": task_packet,
                "result": result,
                "error_code": error_code,
                "started_at": now if started else None,
                "finished_at": now if finished else None,
                "created_at": now,
            })
            # 已存在则只更新传入的状态字段
            row["status"] = status
            if task_packet is not None:
                row["task_packet"] = task_packet
            if result is not None:
                row["result"] = result
            if error_code is not None:
                row["error_code"] = error_code
            if runtime_thread_id is not None:
                row["runtime_thread_id"] = runtime_thread_id
            if started:
                row["started_at"] = row.get("started_at") or now
            if finished:
                row["finished_at"] = now
            _MEMORY["agent_runs"][agent_run_id] = row
            return
        sql = """
            INSERT INTO agent_runs
              (id, decision_run_id, agent_type, expert_id, runtime_provider,
               runtime_thread_id, task_id, status, task_packet, result,
               error_code, started_at, finished_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                    %s, CASE WHEN %s THEN now() END,
                    CASE WHEN %s THEN now() END, now())
            ON CONFLICT (id) DO UPDATE SET
              status = EXCLUDED.status,
              task_packet = COALESCE(EXCLUDED.task_packet, agent_runs.task_packet),
              result = COALESCE(EXCLUDED.result, agent_runs.result),
              error_code = COALESCE(EXCLUDED.error_code, agent_runs.error_code),
              runtime_thread_id = COALESCE(EXCLUDED.runtime_thread_id, agent_runs.runtime_thread_id),
              started_at = COALESCE(agent_runs.started_at, EXCLUDED.started_at),
              finished_at = COALESCE(EXCLUDED.finished_at, agent_runs.finished_at)
        """
        await asyncio.to_thread(
            self._exec,
            sql,
            [_db_id(agent_run_id), _db_id(decision_run_id), agent_type, expert_id,
             runtime_provider, runtime_thread_id, task_id, status,
             json.dumps(task_packet) if task_packet is not None else None,
             json.dumps(result) if result is not None else None,
             error_code, started, finished],
        )

    async def list_agent_runs(self, run_id: str) -> list[dict[str, Any]]:
        if self._memory:
            return [
                dict(v)
                for v in _MEMORY["agent_runs"].values()
                if v["decision_run_id"] == run_id
            ]
        sql = "SELECT * FROM agent_runs WHERE decision_run_id = %s ORDER BY created_at"
        return await asyncio.to_thread(self._fetch_all, sql, [_db_id(run_id)])

    # ============ evidence_records（Phase 4）============

    async def upsert_evidence(
        self,
        run_id: str,
        evidence_id: str,
        *,
        claim_id: str,
        kind: str,
        status: str,
        source_title: str = "",
        source_url: str | None = None,
        source_domain: str = "",
        excerpt: str = "",
        relevance: float = 0.5,
        reliability: float = 0.5,
        authority_level: str = "unknown",
        raw: dict[str, Any] | None = None,
    ) -> None:
        """幂等写入证据行（UNIQUE(decision_run_id, evidence_id)）。"""
        if self._memory:
            key = (run_id, evidence_id)
            section = _MEMORY.setdefault("evidence", {})
            section[key] = {
                "id": evidence_id,
                "decision_run_id": run_id,
                "claim_id": claim_id,
                "kind": kind,
                "status": status,
                "source_title": source_title,
                "source_url": source_url,
                "source_domain": source_domain,
                "excerpt": excerpt,
                "relevance": relevance,
                "reliability": reliability,
                "authority_level": authority_level,
                "raw": dict(raw or {}),
            }
            return
        sql = """
            INSERT INTO evidence_records
              (id, decision_run_id, claim_id, kind, status, source_title,
               source_url, source_domain, excerpt, relevance, reliability,
               authority_level, metadata, retrieved_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
            ON CONFLICT (decision_run_id, id) DO UPDATE SET
              status = EXCLUDED.status,
              reliability = EXCLUDED.reliability,
              authority_level = EXCLUDED.authority_level,
              metadata = EXCLUDED.metadata
        """
        await asyncio.to_thread(
            self._exec,
            sql,
            [evidence_id, _db_id(run_id), claim_id, kind, status, source_title,
             source_url, source_domain, excerpt, relevance, reliability,
             authority_level, json.dumps(raw or {})],
        )

    async def list_evidence(self, run_id: str) -> list[dict[str, Any]]:
        if self._memory:
            section = _MEMORY.get("evidence", {})
            return [dict(v) for (rid, _), v in section.items() if rid == run_id]
        sql = """
            SELECT id, decision_run_id, claim_id, kind, status, source_title,
                   source_url, source_domain, excerpt, relevance, reliability,
                   authority_level
            FROM evidence_records WHERE decision_run_id = %s
        """
        return await asyncio.to_thread(self._fetch_all, sql, [_db_id(run_id)])

    # ============ debate_issues（Phase 5）============

    async def upsert_dispute(
        self,
        run_id: str,
        *,
        dispute_key: str,
        question: str,
        importance: float,
        conflict_type: str,
        participants: list[str],
        claims: list[dict[str, Any]] | None = None,
        status: str = "open",
        round_count: int = 0,
        resolution: dict[str, Any] | None = None,
    ) -> None:
        if self._memory:
            section = _MEMORY.setdefault("disputes", {})
            section[(run_id, dispute_key)] = {
                "dispute_key": dispute_key,
                "decision_run_id": run_id,
                "question": question,
                "importance": importance,
                "conflict_type": conflict_type,
                "participants": list(participants),
                "claims": claims or [],
                "status": status,
                "round_count": round_count,
                "resolution": resolution,
            }
            return
        sql = """
            INSERT INTO debate_issues
              (id, decision_run_id, dispute_key, question, importance,
               conflict_type, participants, claims, status, round_count,
               resolution, updated_at)
            VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s::jsonb,
                    %s::jsonb, %s, %s, %s::jsonb, now())
            ON CONFLICT (decision_run_id, dispute_key) DO UPDATE SET
              status = EXCLUDED.status,
              round_count = EXCLUDED.round_count,
              resolution = EXCLUDED.resolution,
              claims = EXCLUDED.claims,
              participants = EXCLUDED.participants,
              updated_at = now()
        """
        await asyncio.to_thread(
            self._exec,
            sql,
            [_db_id(run_id), dispute_key, question, importance, conflict_type,
             json.dumps(participants), json.dumps(claims or []),
             status, round_count, json.dumps(resolution or {})],
        )

    async def list_disputes(self, run_id: str) -> list[dict[str, Any]]:
        if self._memory:
            section = _MEMORY.get("disputes", {})
            return [dict(v) for (rid, _), v in section.items() if rid == run_id]
        sql = """
            SELECT dispute_key, question, importance, conflict_type,
                   participants, claims, status, round_count, resolution
            FROM debate_issues WHERE decision_run_id = %s
        """
        return await asyncio.to_thread(self._fetch_all, sql, [_db_id(run_id)])

    # ============ 底层执行（仅 DB 模式）============

    def _exec(self, sql: str, params: list, *, want_rowcount: bool = False) -> bool:
        with psycopg.connect(direct_db_url(), connect_timeout=30) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rowcount = cur.rowcount
            conn.commit()
        if want_rowcount:
            return rowcount > 0
        return True

    def _fetch_one(self, sql: str, params: list) -> dict[str, Any] | None:
        with psycopg.connect(direct_db_url(), connect_timeout=30) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return _decode_row(dict(zip(cols, row)))

    def _fetch_all(self, sql: str, params: list) -> list[dict[str, Any]]:
        with psycopg.connect(direct_db_url(), connect_timeout=30) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
        return [_decode_row(dict(zip(cols, r))) for r in rows]


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    """psycopg 返回 UUID/dict 等 Python 对象，转 JSON-safe 类型。"""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, uuid.UUID):
            out[key] = str(value)
        else:
            out[key] = value
    return out
