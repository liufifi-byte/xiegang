"""升级 schema：V2 加 parliament_runtime 表（持久化运行时状态）。

run with:
    cd /Users/xinyiliu/Desktop/ZCodeProject/council/backend
    ./.venv/bin/python scripts/migrate_004.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings


SQL = """
-- V2-S1.2: 议会运行时状态持久化
-- 解决 runtime_context.py 的 module-level Dict 在
-- 多 worker / 进程重启时状态丢失的问题
CREATE TABLE IF NOT EXISTS parliament_runtime (
    parliament_id UUID PRIMARY KEY REFERENCES parliaments(id) ON DELETE CASCADE,
    user_interrupt TEXT,
    cancel_requested BOOLEAN DEFAULT FALSE,
    is_running BOOLEAN DEFAULT FALSE,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_parliament_runtime_running
    ON parliament_runtime (is_running) WHERE is_running = TRUE;

-- 全量更新触发器：updated_at 自动维护
CREATE OR REPLACE FUNCTION update_parliament_runtime_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_parliament_runtime_updated
    ON parliament_runtime;
CREATE TRIGGER trg_parliament_runtime_updated
    BEFORE UPDATE ON parliament_runtime
    FOR EACH ROW
    EXECUTE FUNCTION update_parliament_runtime_updated_at();
"""


def _clean_db_url(url: str) -> str:
    return re.sub(r"\?.*$", "", url)


def main() -> None:
    settings = get_settings()
    db_url = _clean_db_url(settings.supabase_db_url)

    with psycopg.connect(db_url, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute(SQL)
            conn.commit()

            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'parliament_runtime'
            """)
            exists = cur.fetchone() is not None
            print(f"✓ parliament_runtime 表创建: {exists}")

            cur.execute("""
                SELECT tgname FROM pg_trigger
                WHERE tgname = 'trg_parliament_runtime_updated'
            """)
            trigger_exists = cur.fetchone() is not None
            print(f"✓ updated_at 触发器: {trigger_exists}")

    print("\n✅ Migration 004 完成")


if __name__ == "__main__":
    main()
