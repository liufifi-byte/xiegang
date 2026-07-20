"""V2-S3: user_memories.topic_embedding 改成 2048 维 + ivfflat 重建。

embedding-3 实际是 2048 维（验证脚本测得的）。
原 schema 是 VECTOR(1536)（OpenAI text-embedding-3-small 默认）。
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings


SQL = """
-- 改维度 1536 → 2048
ALTER TABLE user_memories
    ALTER COLUMN topic_embedding TYPE VECTOR(2048);

-- 重新创建 ivfflat 索引（lists=100 适合 < 1000 行；之后可调）
DROP INDEX IF EXISTS idx_user_memories_embedding;
CREATE INDEX idx_user_memories_embedding
    ON user_memories USING ivfflat (topic_embedding vector_cosine_ops)
    WITH (lists = 100);
"""


def _clean_db_url(url: str) -> str:
    return re.sub(r"\?.*$", "", url)


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
                        SELECT character_maximum_length
                        FROM information_schema.columns
                        WHERE table_name = 'user_memories'
                          AND column_name = 'topic_embedding'
                    """)
                    row = cur.fetchone()
                    cur.execute("""
                        SELECT indexname FROM pg_indexes
                        WHERE tablename = 'user_memories'
                          AND indexname = 'idx_user_memories_embedding'
                    """)
                    idx_exists = cur.fetchone() is not None
            print(f"✓ topic_embedding 维度 alter 成功")
            print(f"✓ ivfflat 索引重建: {idx_exists}")
            print("\n✅ Migration 005 完成")
            return
        except psycopg.errors.ConnectionTimeout as e:
            wait = attempt * 8
            print(f"✗ 连接超时，等 {wait}s 重试...")
            time.sleep(wait)
        except Exception as e:
            print(f"✗ 错误: {type(e).__name__}: {e}")
            raise

    print("\n❌ 6 次重试都失败")


if __name__ == "__main__":
    main()
