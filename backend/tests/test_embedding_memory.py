"""V2-S3 embedding + memory_query + 工具差异化 测试。"""
import asyncio
from unittest.mock import MagicMock

import pytest

from app.tools.memory_query import _format_memories_for_prompt


# ============ memory_query.py 格式化 ============

def test_format_memories_empty():
    assert _format_memories_for_prompt([]) == "（暂无相关历史记忆）"


def test_format_memories_with_rows():
    rows = [
        {
            "topic_summary": "要不要接 CTO offer",
            "members_stances": {"suan": {"stance": "for"}, "chen": {"stance": "against"}},
            "consensus": ["高方差"],
            "dissent": [],
        },
    ]
    result = _format_memories_for_prompt(rows)
    assert "CTO offer" in result
    assert "suan" in result or "for" in result
    assert "高方差" in result


def test_format_memories_with_missing_fields():
    """rows 缺字段时不应该炸。"""
    rows = [{"topic_summary": "x"}]
    result = _format_memories_for_prompt(rows)
    assert "x" in result


# ============ memory_query.py 检索逻辑 ============

def test_memory_query_returns_default_when_no_query_text():
    """没传 query_text → 返回默认'暂无历史记忆'。"""
    from app.tools.memory_query import memory_query
    result = memory_query(user_id="anon", topic_category="real_estate", query_text=None)
    assert "暂无" in result


def test_memory_query_handles_embedding_failure(monkeypatch):
    """embedding 失败 → 降级返回'暂无历史记忆'。"""
    from app.tools import memory_query as mq

    def fail(text):
        raise RuntimeError("API 击穿")

    monkeypatch.setattr("app.embedding.embed_query_sync", fail)
    result = mq.memory_query("anon", "real_estate", "要不要买房")
    assert "暂无" in result or "失败" in result or "无" in result


def test_memory_query_handles_supabase_failure(monkeypatch):
    """supabase 检索失败 → 第二次 fallback 也失败 → 不抛错。"""
    from app.tools import memory_query as mq

    monkeypatch.setattr("app.embedding.embed_query_sync", lambda text: [0.0] * 2000)

    # 模拟 supabase 完全挂掉
    class FailingSupabase:
        def rpc(self, *args, **kwargs):
            raise RuntimeError("rpc 失败")
        def table(self, name):
            class F:
                def select(self, *a):
                    return self
                def eq(self, *a, **k):
                    return self
                def order(self, *a, **k):
                    return self
                def limit(self, *a):
                    class ExecFail:
                        def execute(self_inner):
                            raise RuntimeError("fallback 也失败")
                    return ExecFail()
            return F()

    monkeypatch.setattr("app.db.get_supabase", lambda: FailingSupabase())
    result = mq.memory_query("anon", "real_estate", "要不要买房")
    assert isinstance(result, str)
    assert "暂无" in result or "无" in result


# ============ embedding.py 维度限制 ============

def test_embedding_trim_cuts_to_2000():
    """V2-S3 ivfflat 限制 → 截 2000 维。"""
    from app.embedding import _trim, EMBEDDING_DIM
    full_vec = [0.1] * 2048
    trimmed = _trim(full_vec)
    assert len(trimmed) == EMBEDDING_DIM
    assert EMBEDDING_DIM == 2000


async def _test_embed_query_uses_trim(monkeypatch):
    """embed_query 返回的向量应该是 2000 维（被 trim 过）。"""
    from app import embedding
    from app.embedding import embed_query

    class MockCreate:
        async def __call__(self, **kwargs):
            class D:
                embedding = [0.5] * 2048
            class R:
                data = [D()]
            return R()

    class MockEmbeddings:
        create = MockCreate()

    class MockAsyncOpenAI:
        embeddings = MockEmbeddings()

    monkeypatch.setattr(embedding, "_get_openai_async", lambda: MockAsyncOpenAI())
    vec = await embed_query("要不要买房")
    assert len(vec) == 2000
    assert vec[0] == 0.5


def test_embed_query_uses_trim_sync(monkeypatch):
    """同步路径也要 trim 到 2000 维。"""
    asyncio.run(_test_embed_query_uses_trim(monkeypatch))


# ============ topic_specific.py 工具差异化 ============

def test_topic_specific_tools_have_different_queries(monkeypatch):
    """5 个工具应该是不同的 query 模板，不只是 web_search 复制。"""
    from app.tools import topic_specific as ts

    captured = []
    def fake_search(query, max_results=5, **kwargs):
        captured.append(query)
        return []

    monkeypatch.setattr(ts, "web_search", fake_search)

    ts.historical_risk_lookup("BTC")
    ts.market_window_check("BTC")
    ts.trend_research("BTC")
    ts.contrarian_search("BTC")
    ts.data_table("BTC")

    assert len(captured) == 5
    # 5 个 query 应该都不一样
    assert len(set(captured)) == 5


def test_topic_specific_year_in_market_window(monkeypatch):
    """market_window 应该带当前年份。"""
    from app.tools import topic_specific as ts
    from datetime import datetime

    captured = []
    monkeypatch.setattr(ts, "web_search", lambda q, max_results=5, **kw: captured.append(q) or [])

    ts.market_window_check("买房")
    assert str(datetime.now().year) in captured[0]


def test_topic_specific_sites_in_contrarian(monkeypatch):
    """contrarian 应该带 reddit / 知乎 来源关键词。"""
    from app.tools import topic_specific as ts

    captured = []
    monkeypatch.setattr(ts, "web_search", lambda q, max_results=5, **kw: captured.append(q) or [])

    ts.contrarian_search("买房")
    assert "reddit" in captured[0] or "zhihu" in captured[0]


def test_topic_specific_all_callable():
    from app.tools.topic_specific import (
        historical_risk_lookup,
        market_window_check,
        trend_research,
        contrarian_search,
        data_table,
    )
    for fn in [historical_risk_lookup, market_window_check,
               trend_research, contrarian_search, data_table]:
        assert callable(fn)
