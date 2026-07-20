"""V2-S3 修正版：user_memories.topic_embedding 改 2048 + HNSW 索引。

注意：ivfflat 限制 2000 维，这里用 HNSW（无维数限制）。
如果 HNSW 不可用，降级方案：把维度降到 2000 + ivfflat。
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings


SQL_HNSW = """
ALTER TABLE user_memories
    ALTER COLUMN topic_embedding TYPE VECTOR(2048);

DROP INDEX IF EXISTS idx_user_memories_embedding;
CREATE INDEX idx_user_memories_embedding
    ON user_memories USING hnsw (topic_embedding vector_cosine_ops);
"""

SQL_IVFFLAT_FALLBACK = """
-- 2048 头一个维度强转 2000（更小数据集精度受影响小）
ALTER TABLE user_memories
    ALTER COLUMN topic_embedding TYPE VECTOR(2000);

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

    for attempt in range(1, 7):
        print(f"=== 尝试 {attempt}/6 ===")
        try:
            with psycopg.connect(db_url, connect_timeout=60) as conn:
                with conn.cursor() as cur:
                    # 先试 HNSW（2048 维）
                    try:
                        cur.execute(SQL_HNSW)
                        conn.commit()
                        mode = "HNSW + 2048 维"
                    except Exception as hnsw_err:
                        print(f"HNSW 失败：{hnsw_err}，回退到 ivfflat 2000 维")
                        conn.rollback()
                        cur.execute(SQL_IVFFLAT_FALLBACK)
                        conn.commit()
                        mode = "ivfflat + 2000 维"

                    # 校验索引
                    cur.execute("""
                        SELECT indexname FROM pg_indexes
                        WHERE tablename = 'user_memories'
                          AND indexname = 'idx_user_memories_embedding'
                    """)
                    idx_exists = cur.fetchone() is not None
            print(f"✓ 索引创建: {mode}（{(mode)}）")
            print(f"✓ ivfflat 索引: {idx_exists}")
            print("\n✅ Migration 005_fix 完成")
            return
        except psycopg.errors.ConnectionTimeout:
            print(f"✗ 超时，等 {attempt*8}s 重试")
            time.sleep(attempt * 8)
        except Exception as e:
            print(f"✗ 错误: {type(e).__name__}: {e}")
            raise

    print("❌ 全部失败")


if __name__ == "__main__":
    main()
