"""带重试的 migrate 004 runner。

Supabase pooler 偶尔连接超时，这里加 5 次重试。
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings

SQL_PATH = Path(__file__).resolve().parent / "migrate_004_inline.sql"


def _clean_db_url(url: str) -> str:
    return re.sub(r"\?.*$", "", url)


SQL = open(Path(__file__).resolve().parent / "migrate_004.py").read().split('SQL = """')[-1].split('"""')[0]


def main() -> None:
    settings = get_settings()
    db_url = _clean_db_url(settings.supabase_db_url)

    max_retries = 6
    for attempt in range(1, max_retries + 1):
        print(f"\n=== 尝试 {attempt}/{max_retries} ===")
        try:
            with psycopg.connect(db_url, connect_timeout=60) as conn:
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
            return
        except psycopg.errors.ConnectionTimeout as e:
            wait = attempt * 10
            print(f"✗ 连接超时: {e}")
            print(f"... 等 {wait}s 重试")
            time.sleep(wait)
        except Exception as e:
            print(f"✗ 其他错误: {e}")
            raise

    print("\n❌ 6 次重试都失败。请稍后再跑或换网络。")


if __name__ == "__main__":
    main()
