"""Web 搜索工具（Tavily）。

Tavily 免费层 1000 次/月，够 MVP 用。
"""
from tavily import TavilyClient

from app.config import get_settings

_client: TavilyClient | None = None


def get_client() -> TavilyClient:
    global _client
    if _client is None:
        _client = TavilyClient(api_key=get_settings().tavily_api_key)
    return _client


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """搜索公开信息，返回 [{title, url, content, score}, ...]。"""
    result = get_client().search(query=query, max_results=max_results)
    return result.get("results", [])
