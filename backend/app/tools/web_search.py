"""Independent GLM Web Search API client returning inspectable sources."""

from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import get_settings

logger = logging.getLogger(__name__)


class WebSearchError(RuntimeError):
    """Raised when the independent search service cannot return valid results."""


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the public web through GLM's structured Web Search API."""
    settings = get_settings()
    endpoint = f"{settings.glm_base_url.rstrip('/')}/web_search"
    payload = json.dumps(
        {
            "search_query": str(query).strip()[:120],
            "search_engine": settings.web_search_engine,
            "search_intent": False,
            "count": max(1, min(int(max_results), 5)),
            "search_recency_filter": "noLimit",
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
