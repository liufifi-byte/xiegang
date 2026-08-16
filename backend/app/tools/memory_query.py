"""跨议会记忆查询：从 Supabase pgvector 检索相似历史议会。

V2-S3: 真用 GLM embedding-3 + pgvector 余弦相似度检索。
V2-S3b: 检索按 user_id 隔离（RPC 加 filter_user_id 参数，兜底路径 eq 过滤）。
         此前按 category 全局检索是单用户时代的临时设计——多用户下等于
         跨用户记忆泄漏；且"先 LIMIT 再在应用层过滤"会挤掉自己的行。
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


def _rpc_params(
    query_vec: list[float],
    limit: int,
    topic_category: str | None,
    user_id: str | None,
) -> dict:
    """match_user_memories 的调用参数（sync/async 共用，保证两路一致）。"""
    return {
        "query_embedding": query_vec,
        "match_count": limit,
        "filter_category": topic_category if topic_category else None,
        # 老版本 RPC 没这个参数也不报错（Postgres 多余命名参数会被忽略…实际会
        # 报错，故依赖 DB 侧已 CREATE OR REPLACE 升级；兜底路径有 eq 过滤）。
        "filter_user_id": user_id if user_id else None,
    }


def memory_query(
    user_id: str,
    topic_category: str,
    query_text: str | None = None,
    limit: int = 3,
) -> str:
    """同步检索相似历史议会，返回 prompt 文本。

    Args:
        user_id: 记忆归属键（议会行的 user_id，浏览器用户 = 设备 UUID）。
                 None/空 = 不带 user 过滤（等价旧行为，仅限无用户上下文场景）。
        topic_category: 议题类别 real_estate / gold / ...
        query_text: 当前议题原文（用来算 embedding）
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

    # 2. pgvector 余弦相似度检索（DB 侧按 user_id 隔离）
    try:
        from app.db import get_supabase
        supabase = get_supabase()
        result = supabase.rpc(
            "match_user_memories",
            _rpc_params(query_vec, limit, topic_category, user_id),
        ).execute()
        rows = result.data or []
        return _format_memories_for_prompt(rows)
    except Exception as e:
        # 兜底：按 user + category 时间倒序拿最近 N 条
        print(f"[memory_query] pgvector 检索失败: {e}")
        try:
            from app.db import get_supabase
            supabase = get_supabase()
            query = supabase.table("user_memories").select("*")
            if user_id:
                query = query.eq("user_id", user_id)
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
    user_id: str | None = None,
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
        params = _rpc_params(query_vec, limit, topic_category, user_id)

        def fetch():
            return supabase.rpc("match_user_memories", params).execute()

        result = await asyncio.to_thread(fetch)
        rows = result.data or []
        return _format_memories_for_prompt(rows)
    except Exception as e:
        print(f"[memory_query_async] pgvector 检索失败: {e}")
        return _format_memories_for_prompt([])
