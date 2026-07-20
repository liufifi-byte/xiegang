"""V2/P0-S2: 手动建 langgraph checkpointer 表（绕过 CONCURRENTLY 限制）。

langgraph-checkpoint-postgres 的 setup() 用 CREATE INDEX CONCURRENTLY，
Supabase pooler 不支持。这里把所有 migration 改成普通 CREATE INDEX
通过 pooler 跑。
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings


def _clean_db_url(url: str) -> str:
    return re.sub(r"\?.*$", "", url)


def main() -> None:
    settings = get_settings()
    # 用 pooler URL
    db_url = _clean_db_url(settings.supabase_db_url)

    # 从 langgraph 拿 migration 列表，去掉 CONCURRENTLY
    from langgraph.checkpoint.postgres import PostgresSaver
    safe_migrations = [
        m.replace("CREATE INDEX CONCURRENTLY", "CREATE INDEX")
        for m in PostgresSaver.MIGRATIONS
    ]

    for attempt in range(1, 7):
        print(f"=== 尝试 {attempt}/6 ===")
        try:
            with psycopg.connect(db_url, connect_timeout=60) as conn:
                with conn.cursor() as cur:
                    # migration 0: 建 checkpoint_migrations 表
                    cur.execute(safe_migrations[0])

                    # 读取当前 version
                    cur.execute(
                        "SELECT v FROM checkpoint_migrations ORDER BY v DESC LIMIT 1"
                    )
                    row = cur.fetchone()
                    current_v = row[0] if row else -1
                    print(f"  当前 migration 版本: {current_v}")

                    # 跑后续 migrations
                    for v in range(current_v + 1, len(safe_migrations)):
                        sql = safe_migrations[v]
                        print(f"  [{v}] {sql.strip()[:80]}...")
                        cur.execute(sql)
                        cur.execute(
                            "INSERT INTO checkpoint_migrations (v) VALUES (%s)",
                            (v,),
                        )
                conn.commit()
            print("\n✅ langgraph checkpoint 表已创建/迁移")
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
