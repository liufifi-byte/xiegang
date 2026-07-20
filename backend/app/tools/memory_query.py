"""跨议会记忆查询：从 Supabase pgvector 检索相似历史议会。

V2-S3: 真用 GLM embedding-3 + pgvector 余弦相似度检索。
"""
from app.embedding import embed_query_sync


def _format_memories_for_prompt(rows: list[dict]) -> str:
    """把检索到的历史议会摘要格式化为 prompt 文本。"""
    if not rows:
        return "（暂无相关历史记忆）"

    lines = []
    for row in rows[:3]:
        topic = row.get("topic_summary") or "（无主题）"
        stances = row.get("members_stances") or {}
        consensus = row.get("consensus") or []
        # 取每个议员的最终立场
        stance_summary_parts = []
        if isinstance(stances, dict):
            for mid, info in stances.items():
                stance = info.get("stance", "?") if isinstance(info, dict) else "?"
                stance_summary_parts.append(f"{mid}:{stance}")
        stance_str = " / ".join(stance_summary_parts) if stance_summary_parts else "（无）"
        cons_text = " | ".join(consensus[:2]) if consensus else "（无）"
        lines.append(
            f"- 之前问过「{topic[:60]}」; 议员立场: {stance_str}; 共识: {cons_text}"
        )
    return "\n".join(lines)


def memory_query(
    user_id: str,
    topic_category: str,
    query_text: str | None = None,
    limit: int = 3,
) -> str:
    """同步检索相似历史议会，返回 prompt 文本。

    Args:
        user_id: 现在所有议会都共用一个 anonymous user_id = None 或者 "anon"
                 V2 阶段先按 category 检索不按 user（单用户产品）
        topic_category: 议题类别 real_estate / gold / ...
        query_text: 当前议题原文（用来算 embedding）
                    如果传 None 就退回到前几天一样按时间倒叙检索
        limit: 最多返回几条

    Returns:
        格式化好的字符串，放进 prompt 的 {memories} 占位符。
    """
    # 没有 query_text 就走纯回退（"暂无历史记忆"）
    if not query_text:
        return _format_memories_for_prompt([])

    # 1. 算 query embedding
    try:
        query_vec = embed_query_sync(query_text)
    except Exception as e:
        # embedding 失败也别让议会炸——退回到"无记忆"
        print(f"[memory_query] embedding 失败: {e}")
        return _format_memories_for_prompt([])

    # 2. pgvector 余弦相似度检索
    try:
        from app.db import get_supabase
        supabase = get_supabase()
        # Supabase rpc 调用：match_user_memories
        # 用裸 SQL render 避开 rpc 函数定义
        # 这里直接调 .rpc() 假设已建；如果不行就 fallback
        result = supabase.rpc(
            "match_user_memories",
            {
                "query_embedding": query_vec,
                "match_count": limit,
                "filter_category": topic_category if topic_category else None,
            },
        ).execute()
        rows = result.data or []
        return _format_memories_for_prompt(rows)
    except Exception as e:
        # 兜底：按 category 时间倒序拿最近 N 条
        print(f"[memory_query] pgvector 检索失败: {e}")
        try:
            from app.db import get_supabase
            supabase = get_supabase()
            query = supabase.table("user_memories").select("*")
            if topic_category:
                query = query.eq("topic_category", topic_category)
            result = query.order("created_at", desc=True).limit(limit).execute()
            rows = result.data or []
            return _format_memories_for_prompt(rows)
        except Exception as e2:
            print(f"[memory_query] fallback 也失败: {e2}")
            return _format_memories_for_prompt([])


async def memory_query_async(
    topic_category: str,
    query_text: str,
    limit: int = 3,
) -> str:
    """异步版本——调 async embedding（parallel_research 用）。"""
    from app.embedding import embed_query
    try:
        query_vec = await embed_query(query_text)
    except Exception as e:
        print(f"[memory_query_async] embedding 失败: {e}")
        return _format_memories_for_prompt([])

    # Supabase sync client，没真 async——包一下
    import asyncio
    try:
        from app.db import get_supabase
        supabase = get_supabase()
        def fetch():
            return supabase.rpc(
                "match_user_memories",
                {
                    "query_embedding": query_vec,
                    "match_count": limit,
                    "filter_category": topic_category if topic_category else None,
                },
            ).execute()
        result = await asyncio.to_thread(fetch)
        rows = result.data or []
        return _format_memories_for_prompt(rows)
    except Exception as e:
        print(f"[memory_query_async] pgvector 检索失败: {e}")
        return _format_memories_for_prompt([])
