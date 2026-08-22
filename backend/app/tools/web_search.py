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
import re
import logging
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import get_settings

logger = logging.getLogger(__name__)


class WebSearchError(RuntimeError):
    """Raised when the independent search service cannot return valid results."""


_CONTENT_DATE_RE = re.compile(
    r"(?:发布时间|发表于|published)[:：\s]*([12]\d{3})[-/年.]"
    r"|\(([12]\d{3})[-/.]|\b([12]\d{3})[-/年](?:[01]?\d|一|二|三|四|五|六|七|八|九|十)"
)


def _year_from_content(content: str) -> int | None:
    """正文兜底抠年份：自媒体常把"发布时间：2023-09-19"写进正文而不给字段。

    没有这个兜底，旧帖会以"无日期"身份绕过 require_fresh 过滤
    （2026-08-22 用户实测跳槽议题混入 2021/2023 老帖的直接原因）。
    """
    if not content:
        return None
    m = _CONTENT_DATE_RE.search(content[:400])
    if not m:
        return None
    for group in m.groups():
        if group:
            try:
                return int(group)
            except ValueError:
                continue
    return None


def _parse_publish_year(value, content: str = "") -> int | None:
    """从 GLM 返回的 publish_date 中抠出年份。容错：坏格式/None 返回 None。"""
    if not value:
        return _year_from_content(content)
    text = str(value).strip()
    if not text:
        return _year_from_content(content)
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
                "_publish_year": _parse_publish_year(publish_date_raw, content),
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
    undated_kept = 0
    _MAX_UNDATED = 1  # 无日期结果至多保留 1 条（历史漏洞：无日期=绕过时效）
    if require_fresh:
        kept: list[dict] = []
        for item in candidates:
            year = item.pop("_publish_year", None)
            if year is not None and year < min_year:
                stale_dropped += 1
                continue
            if year is None:
                if undated_kept >= _MAX_UNDATED:
                    stale_dropped += 1
                    continue
                undated_kept += 1
            kept.append(item)
        candidates = kept
    else:
        # 清掉临时字段
        for item in candidates:
            item.pop("_publish_year", None)

    # 第三遍：权威感知排序 + 自媒体封顶（始终生效，不依赖 require_fresh）：
    # trusted > media > blog；返回列表里自媒体（blog）至多 2 条——
    # 个人号经验帖只能当线索，不能占满证据位（2026-08-22 用户实测反馈）
    _grade_rank = {"trusted": 0, "media": 1, "blog": 2}
    candidates.sort(key=lambda c: _grade_rank.get(c.get("source_grade"), 1))
    blog_kept = 0
    _MAX_BLOG = 2
    ranked: list[dict] = []
    blog_overflow = 0
    for item in candidates:
        if item.get("source_grade") == "blog":
            if blog_kept >= _MAX_BLOG:
                blog_overflow += 1
                continue
            blog_kept += 1
        ranked.append(item)
    candidates = ranked
    if blog_overflow:
        logger.info("web_search dropped %s blog results (自媒体封顶 %s)",
                    blog_overflow, _MAX_BLOG)

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
