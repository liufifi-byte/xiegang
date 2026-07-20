"""P0-S2.1: 加 parliament_events 表 + parliaments 加 device_id / last_event_seq。"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings


SQL = """
-- P0-S2.1: 事件流表（Worker 写，SSE 读）
CREATE TABLE IF NOT EXISTS parliament_events (
    id BIGSERIAL PRIMARY KEY,
    parliament_id UUID NOT NULL REFERENCES parliaments(id) ON DELETE CASCADE,
    seq INT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(parliament_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_parliament_events_lookup
    ON parliament_events (parliament_id, seq);

-- parliaments 加所有权校验 + 事件序号
ALTER TABLE parliaments
    ADD COLUMN IF NOT EXISTS device_id TEXT,
    ADD COLUMN IF NOT EXISTS last_event_seq INT DEFAULT 0;

-- queued 状态索引（worker 抢任务用）
CREATE INDEX IF NOT EXISTS idx_parliaments_queued
    ON parliaments (created_at)
    WHERE status = 'queued';
"""


def _clean_db_url(url: str) -> str:
    return re.sub(r"\?.*$", "", url)


def main() -> None:
    settings = get_settings()
    db_url = _clean_db_url(settings.supabase_db_url)

    for attempt in range(1, 7):
        print(f"=== 尝试 {attempt}/6 ===")
        try:
            with psycopg.connect(db_url, connect_timeout=60) as conn:
                with conn.cursor() as cur:
                    cur.execute(SQL)
                    conn.commit()

                    cur.execute("""
                        SELECT table_name FROM information_schema.tables
                        WHERE table_name = 'parliament_events'
                    """)
                    assert cur.fetchone() is not None
                    cur.execute("""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_name = 'parliaments' AND column_name = 'device_id'
                    """)
                    assert cur.fetchone() is not None
            print("✓ parliament_events 表创建")
            print("✓ parliaments.device_id / last_event_seq 字段")
            print("✓ queued 索引")
            print("\n✅ Migration 007 完成")
            return
        except psycopg.errors.ConnectionTimeout:
            wait = attempt * 8
            print(f"✗ 超时，等 {wait}s")
            time.sleep(wait)
        except Exception as e:
            print(f"✗ {type(e).__name__}: {e}")
            raise

    print("❌ 全部失败")


if __name__ == "__main__":
    main()
