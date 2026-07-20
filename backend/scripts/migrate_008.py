"""P0-S2 fix: 给 parliaments 加 started_at 字段（worker 用）。"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings


SQL = """
ALTER TABLE parliaments
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
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
                        SELECT column_name FROM information_schema.columns
                        WHERE table_name = 'parliaments' AND column_name = 'started_at'
                    """)
                    assert cur.fetchone() is not None
            print("✓ parliaments.started_at 字段添加")
            print("\n✅ Migration 008 完成")
            return
        except psycopg.errors.ConnectionTimeout:
            print(f"✗ 超时，等 {attempt * 8}s")
            time.sleep(attempt * 8)
        except Exception as e:
            print(f"✗ {type(e).__name__}: {e}")
            raise

    print("❌ 全部失败")


if __name__ == "__main__":
    main()
