"""Independent GLM Web Search API client returning inspectable sources.

时效策略（修订）：
默认 require_fresh=False —— 拿到完整结果集（不限最近一年）。
原因：实测换成 search_recency_filter=oneYear 时，中文垂直 query（如"北京海淀房价"）
命中数会从 ~3 条降到 0 条——GLM 中文搜索召回本就稀少，
再用一年期过滤会过度打压有效结果。
若 query 本就求最新（如"2026 政策展望"），议员工具可显式传 require_fresh=True。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import get_settings

logger = logging.getLogger(__name__)


class WebSearchError(RuntimeError):
    """Raised when the independent search service cannot return valid results."""


def web_search(
    query: str,
    max_results: int = 5,
    require_fresh: bool = False,
) -> list[dict]:
    """Search the public web through GLM's structured Web Search API.

    Args:
        query: 搜索词，中英文均可。
        max_results: 返回条数上限。
        require_fresh: 是否只取最近一年结果。
            默认 False —— GLM 中文垂直搜索召回本就稀少，再过滤会过度打压。
            议员工具若 query 本就求最新（如"2026 政策"），可显式传 True。
    """
    settings = get_settings()

    recency_filter = "oneYear" if require_fresh else "noLimit"

    endpoint = f"{settings.glm_base_url.rstrip('/')}/web_search"
    payload = json.dumps(
        {
            "search_query": str(query).strip()[:120],
            "search_engine": settings.web_search_engine,
            "search_intent": False,
            "count": max(1, min(int(max_results), 5)),
            "search_recency_filter": recency_filter,
            "content_size": "medium",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        endpoint,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.glm_api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(
            request,
            timeout=settings.web_search_timeout_seconds,
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise WebSearchError("independent web search unavailable") from exc

    raw_results = body.get("search_result") or []
    results: list[dict] = []
    for index, item in enumerate(raw_results):
        url = str(item.get("link") or "").strip()
        content = str(item.get("content") or "").strip()
        if not url or not content:
            continue
        results.append(
            {
                "title": str(item.get("title") or item.get("media") or "公开资料"),
                "url": url,
                "content": content,
                "score": max(0.5, 0.92 - index * 0.08),
                "media": str(item.get("media") or ""),
                "publish_date": item.get("publish_date"),
            }
        )
        if len(results) >= max_results:
            break
    filtered_count = max(0, len(raw_results) - len(results))
    if filtered_count:
        logger.info(
            "web_search filtered %s incomplete provider results; kept %s",
            filtered_count,
            len(results),
        )
    if raw_results and not results:
        logger.warning(
            "web_search provider returned %s results but none had both URL and content",
            len(raw_results),
        )
    return results
    request = Request(
        endpoint,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.glm_api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(
            request,
            timeout=settings.web_search_timeout_seconds,
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise WebSearchError("independent web search unavailable") from exc

    raw_results = body.get("search_result") or []
    results: list[dict] = []
    for index, item in enumerate(raw_results):
        url = str(item.get("link") or "").strip()
        content = str(item.get("content") or "").strip()
        if not url or not content:
            continue
        results.append(
            {
                "title": str(item.get("title") or item.get("media") or "公开资料"),
                "url": url,
                "content": content,
                "score": max(0.5, 0.92 - index * 0.08),
                "media": str(item.get("media") or ""),
                "publish_date": item.get("publish_date"),
            }
        )
        if len(results) >= max_results:
            break
    filtered_count = max(0, len(raw_results) - len(results))
    if filtered_count:
        logger.info(
            "web_search filtered %s incomplete provider results; kept %s",
            filtered_count,
            len(results),
        )
    if raw_results and not results:
        logger.warning(
            "web_search provider returned %s results but none had both URL and content",
            len(raw_results),
        )
    return results
