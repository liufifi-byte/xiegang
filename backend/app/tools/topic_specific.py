"""议员专属工具：每个议员 1 个特色工具。

V1: 5 个全是 web_search 薄包装（只是 query 前缀不同）
V2-S3: 真差异化——每个工具有专属数据源或更适合该议员思维模型的 query 模板
"""
from app.tools.web_search import web_search


def historical_risk_lookup(query: str) -> list[dict]:
    """老沉专用：搜历史下行 / 翻车案例。

    V2-S3 改进：加 wikipedia / zhihu 长文来源关键词（深度案例 > 短篇新闻）
    """
    return web_search(
        f"{query} historical crash 2008 2015 2018 2022 case study "
        f"(site:wikipedia.org OR site:zhihu.com)",
        max_results=4,
    )


def market_window_check(query: str) -> list[dict]:
    """小机专用：搜市场窗口期 / 政策时机。

    V2-S3 改进：加 "year" 关键词保证时效性
    """
    from datetime import datetime
    year = datetime.now().year
    return web_search(
        f"{query} market window timing opportunity interest rate {year} "
        f"outlook report",
        max_results=4,
    )


def trend_research(query: str) -> list[dict]:
    """十年专用：搜长期趋势数据。

    V2-S3 改进：用"10 year"明确长期维度，加 statistics 关键词
    """
    return web_search(
        f"{query} 10 year long term trend prediction statistics "
        f"compounding compound growth",
        max_results=4,
    )


def contrarian_search(query: str) -> list[dict]:
    """反对专用：搜反对意见和翻车案例。

    V2-S3 改进：加 reddit / youtube / 知乎等长尾意见源
    """
    return web_search(
        f"{query} criticism failure problem scam bubble "
        f"against (site:reddit.com OR site:zhihu.com OR site:bilibili.com)",
        max_results=4,
    )


def data_table(query: str) -> list[dict]:
    """老算专用：搜结构化数据 / 对比表。

    V2-S3 改进：明确"comparison"和"statistics"关键词
    """
    return web_search(
        f"{query} comparison table data statistics numbers pricing",
        max_results=4,
    )
