"""P0-S2.1: 事件存储——Worker 写事件，SSE 读事件。

每个议会有一系列按 seq 递增的事件：
  parliament_started → node_complete(parse_topic) → node_complete(research)
  → node_complete(debate R1) → ... → parliament_done

Worker 每完成一个节点就 append_event。
SSE 端 500ms 轮询 fetch_events_since(last_seq) 拿新事件推给前端。
"""
import json
import re
from typing import Any

import psycopg

from app.config import get_settings


def _clean_db_url() -> str:
    return re.sub(r"\?.*$", "", get_settings().supabase_db_url)


def _safe_json(obj: Any) -> Any:
    """递归把不可 JSON 序列化的对象转字符串。"""
    if isinstance(obj, dict):
        return {k: _safe_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_json(v) for v in obj]
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def _make_safe(payload: dict) -> dict:
    """遍历 dict 确保每个 value 可序列化。"""
    if not isinstance(payload, dict):
        return {"value": _safe_json(payload)}
    return {k: _safe_json(v) for k, v in payload.items()}


def append_event(parliament_id: str, event_type: str, payload: dict) -> int:
    """写入一条事件，返回 seq。同步接口（Worker 调用）。

    seq 自动从 last_event_seq + 1 算（加 UPDATE 行锁保护并发）。
    """
    safe_payload = _make_safe(payload)
    payload_json = json.dumps(safe_payload, ensure_ascii=False, default=str)

    url = _clean_db_url()
    with psycopg.connect(url, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            # 原子操作：UPDATE last_event_seq + INSERT event
            cur.execute("BEGIN")
            cur.execute(
                """UPDATE parliaments
                   SET last_event_seq = last_event_seq + 1
                   WHERE id = %s
                   RETURNING last_event_seq""",
                (parliament_id,),
            )
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                raise ValueError(f"parliament {parliament_id} not found")
            seq = row[0]

            cur.execute(
                """INSERT INTO parliament_events (parliament_id, seq, event_type, payload)
                   VALUES (%s, %s, %s, %s::jsonb)""",
                (parliament_id, seq, event_type, payload_json),
            )
            conn.commit()
    return seq


def fetch_events_since(parliament_id: str, last_seq: int, limit: int = 100) -> list[dict]:
    """读取 seq > last_seq 的事件。SSE 轮询用。"""
    url = _clean_db_url()
    with psycopg.connect(url, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT seq, event_type, payload, created_at
                   FROM parliament_events
                   WHERE parliament_id = %s AND seq > %s
                   ORDER BY seq ASC
                   LIMIT %s""",
                (parliament_id, last_seq, limit),
            )
            rows = cur.fetchall()

    return [
        {
            "seq": r[0],
            "event_type": r[1],
            "payload": r[2] if isinstance(r[2], dict) else json.loads(r[2]),
            "created_at": r[3].isoformat() if r[3] else None,
        }
        for r in rows
    ]
