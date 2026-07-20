"""记忆归档节点：把议会结果写回数据库。

- 更新 parliaments 表对应行的状态/字段
- 同时写一条 user_memories 记录（供下次议会检索）
- V2-S3: 真计算 topic_embedding 入库（embedding 维度 2048）
"""
import time

from app.db import get_supabase


def run_archive(state: dict) -> dict:
    """LangGraph 节点：归档。

    没设 parliament_id 时直接跳过（纯 in-memory 跑测试场景）。
    """
    supabase = get_supabase()
    parliament_id = state.get("parliament_id")

    if not parliament_id:
        return {"finished_at": time.time()}

    try:
        # 1. 更新 parliaments 行（V2-S3 P0-S1 修复：字段名 + 补全 finished_at/duration/status）
        started_at = state.get("started_at")
        duration = int(time.time() - started_at) if started_at else None
        update_data = {
            "status": "done",
            "topic_category": state.get("category"),  # 字段名修复：原 "category" 不存在
            "members_state": state.get("members", {}),
            "transcript": state.get("transcript", []),
            "tool_calls": state.get("tool_calls", []),
            "election_result": state.get("election_result"),
            "summary": state.get("summary"),
            "tension_score": state.get("tension_score", 0.0),
            # P0-S1: 真正补全（原版只 update_state，从不写 DB）
            "finished_at": "now()",
            "total_duration_seconds": duration,
        }
        supabase.table("parliaments").update(update_data).eq(
            "id", parliament_id
        ).execute()

        # 2. 写 user_memories（下次议会能检索）
        summary_data = state.get("summary") or {}

        # V2-S3: 真算 embedding
        topic_text = state["topic"][:200]
        topic_embedding = None
        try:
            from app.embedding import embed_query_sync
            topic_embedding = embed_query_sync(topic_text)
        except Exception as e:
            print(f"[archive] embedding 计算失败: {e}")
            # 失败也继续，至少先把行写进去

        memory_row = {
            "parliament_id": parliament_id,
            "user_id": state.get("user_id"),
            "topic_category": state.get("category", "general"),
            "topic_summary": topic_text,
            "members_stances": state.get("members", {}),
            "consensus": summary_data.get("consensus", []),
            "dissent": summary_data.get("dissent", []),
        }
        # 注意：Supabase postgrest 不支持直接传 vector。要走 RPC 或者裸 SQL。
        # 这里先用 supabase-py 插入不带 embedding 的行，然后单独 update embedding。
        insert_result = supabase.table("user_memories").insert(memory_row).execute()
        new_memory_id = insert_result.data[0]["id"] if insert_result.data else None

        if new_memory_id and topic_embedding is not None:
            # 用裸 psycopg 把 vector 写进去（postgrest-2.4 不支持 vector 类型传值）
            _update_embedding_via_pgsql(new_memory_id, topic_embedding)

        if state.get("started_at"):
            duration = int(time.time() - state["started_at"])
        else:
            duration = None

        return {
            "finished_at": time.time(),
            "total_duration_seconds": duration,
        }

    except Exception as e:
        return {
            "finished_at": time.time(),
            "error_message": f"archive failed: {e}",
        }


def _update_embedding_via_pgsql(memory_id: str, vector: list[float]) -> None:
    """裸 SQL 把 embedding 写入。"""
    import re
    import psycopg
    from app.config import get_settings

    settings = get_settings()
    db_url = re.sub(r"\?.*$", "", settings.supabase_db_url)

    # pgvector 接受 '[0.1,0.2,...]' 字符串
    vec_str = "[" + ",".join(str(float(v)) for v in vector) + "]"
    with psycopg.connect(db_url, connect_timeout=60) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE user_memories SET topic_embedding = %s::vector WHERE id = %s",
                (vec_str, memory_id),
            )
        conn.commit()
