"""V2-S3.2: 建 match_user_memories pgvector 检索 RPC 函数 + archive 计算 embedding 入库。"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from app.config import get_settings


SQL = """
-- V2-S3.2: pgvector 语义检索 RPC 函数
CREATE OR REPLACE FUNCTION match_user_memories(
    query_embedding VECTOR(2048),
    match_count INT DEFAULT 3,
    filter_category TEXT DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    parliament_id UUID,
    topic_category TEXT,
    topic_summary TEXT,
    members_stances JSONB,
    consensus JSONB,
    dissent JSONB,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.id,
        m.parliament_id,
        m.topic_category,
        m.topic_summary,
        m.members_stances,
        m.consensus,
        m.dissent,
        1 - (m.topic_embedding <=> query_embedding) AS similarity
    FROM user_memories m
    WHERE m.topic_embedding IS NOT NULL
      AND (filter_category IS NULL OR m.topic_category = filter_category)
    ORDER BY m.topic_embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
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
                        SELECT proname FROM pg_proc
                        WHERE proname = 'match_user_memories'
                    """)
                    exists = cur.fetchone() is not None
            print(f"✓ match_user_memories RPC 函数: {exists}")
            print("\n✅ Migration 006 完成")
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
