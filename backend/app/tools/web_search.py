"""Independent GLM Web Search API client returning inspectable sources.

时效策略（修订）：
默认 require_fresh=False —— 拿到完整结果集（不限最近一年）。
原因：实测换成 search_recency_filter=oneYear 时，中文垂直 query（如"北京海淀房价"）
命中数会从 ~3 条降到 0 条——GLM 中文搜索召回本就稀少，
再用一年期过滤会过度打压有效结果。

若 require_fresh=True，本工具会做两层新鲜度保证：
  1. 服务端过滤：search_recency_filter=oneYear 让 GLM 优先返回一年内结果
  2. 本地兜底过滤：拿到的结果里 publish_date 早于 web_search_min_publish_year
     的全部丢弃——GLM 服务端过滤不严格，会漏进 2023/2024 的旧文
"""
from __future__ import annotations

import json
import logging
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import get_settings

logger = logging.getLogger(__name__)


class WebSearchError(RuntimeError):
    """Raised when the independent search service cannot return valid results."""


def _parse_publish_year(value) -> int | None:
    """从 GLM 返回的 publish_date 中抠出年份。容错：坏格式/None 返回 None。"""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    # GLM 常见格式: "2026-07-31..." / "2026年7月31日..." / "2026/07/31"
    for sep in ("-", "/", "年", " ", "T"):
        head = text.split(sep, 1)[0]
        if head.isdigit() and len(head) == 4:
            try:
                return int(head)
            except ValueError:
                continue
    # 兜底：取头 4 个数字
    digits = "".join(c for c in text if c.isdigit())[:4]
    if len(digits) == 4:
        try:
            return int(digits)
        except ValueError:
            return None
    return None


def web_search(
    query: str,
    max_results: int = 5,
    require_fresh: bool = False,
) -> list[dict]:
    """Search the public web through GLM's structured Web Search API.

    Args:
        query: 搜索词，中英文均可。
        max_results: 返回条数上限。
        require_fresh: 是否只取近期结果。
            False（默认）：服务端 noLimit，本地不过滤日期——历史案例、复盘可正常拿。
            True：服务端 oneYear + 本地丢早于 web_search_min_publish_year 的旧数据。
    """
    settings = get_settings()

    recency_filter = "oneYear" if require_fresh else "noLimit"
    # 本地兜底过滤阈值：require_fresh=True 时丢弃早于此年的结果
    min_year = getattr(settings, "web_search_min_publish_year", date.today().year - 1)

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

    # 第一遍：构造结构化记录（url + content 双重过滤；解析出 publish_year）
    # 同时过滤招聘软文/导流文 + 标注信源分级
    from app.graph.nodes.text_sanitizer import classify_source, is_recruit_spam

    candidates: list[dict] = []
    incomplete = 0
    spam_dropped = 0
    for index, item in enumerate(raw_results):
        url = str(item.get("link") or "").strip()
        content = str(item.get("content") or "").strip()
        if not url or not content:
            incomplete += 1
            continue
        # P0: 招聘软文 / 公众号导流文 / 房产中介页——不得进入证据池
        if is_recruit_spam({"title": item.get("title"), "content": content, "url": url}):
            spam_dropped += 1
            continue
        publish_date_raw = item.get("publish_date")
        candidates.append(
            {
                "title": str(item.get("title") or item.get("media") or "公开资料"),
                "url": url,
                "content": content,
                "score": max(0.5, 0.92 - index * 0.08),
                "media": str(item.get("media") or ""),
                "publish_date": publish_date_raw,
                "source_grade": classify_source(url),  # trusted/media/blog
                "_publish_year": _parse_publish_year(publish_date_raw),
            }
        )

    if incomplete:
        logger.info(
            "web_search dropped %s incomplete provider results (no url/content)",
            incomplete,
        )
    if spam_dropped:
        logger.info(
            "web_search dropped %s recruit-spam results (导流文/招聘页)",
            spam_dropped,
        )

    # 第二遍：require_fresh 时本地过滤旧数据
    stale_dropped = 0
    if require_fresh:
        kept: list[dict] = []
        for item in candidates:
            year = item.pop("_publish_year", None)
            # publish_date 缺失或无法解析时保留——避免把没日期的好来源误删
            if year is not None and year < min_year:
                stale_dropped += 1
                continue
            item.pop("_publish_year", None)
            kept.append(item)
        candidates = kept
    else:
        # 清掉临时字段
        for item in candidates:
            item.pop("_publish_year", None)

    results = candidates[:max_results]

    if stale_dropped:
        logger.info(
            "web_search dropped %s stale results (publish_year < %s, require_fresh=True)",
            stale_dropped,
            min_year,
        )
    if raw_results and not results:
        logger.warning(
            "web_search provider returned %s results but all filtered out "
            "(incomplete=%s, stale=%s)",
            len(raw_results),
            incomplete,
            stale_dropped,
        )
    return results
