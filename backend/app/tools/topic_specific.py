"""议员专属工具：每个议员 1 个特色工具。

V1: 5 个全是 web_search 薄包装（只是 query 前缀不同）
V2-S3: 真差异化——每个工具有专属 query 模板
本次修订:
  - 3 个"求新"工具 require_fresh=True，本地过滤 publish_year < web_search_min_publish_year
  - 2 个"求例"工具 require_fresh=False（保留历史复盘案例，不丢老数据）
  - 全部 query 都加"截至 <当前年月>"，让 GLM 优先召近期内容
"""
from datetime import date

from app.tools.web_search import web_search

# "截至 2026-08" 形式——比单 2026 更明确，逼 GLM 优先返回相应时期数据
_AS_OF = f"截至 {date.today().strftime('%Y-%m')}"


def historical_risk_lookup(query: str) -> list[dict]:
    """老沉专用：历史上轮周期顶点 / 回撤 / 复盘案例。

    故意保留 require_fresh=False——历史复盘案例本就要老数据，
    本地日期过滤会误删"2008/2018/2022 顶点 → 后续回撤"这类关键案例。
    query 加"截至 2026-08"仅为引导 GLM 优先抓最新复盘文（不强制）。
    """
    return web_search(
        f"{query} 历史上轮周期 顶点 回撤 翻盘 终结 复盘 {_AS_OF} 最新",
        max_results=4,
        require_fresh=False,
    )


def market_window_check(query: str) -> list[dict]:
    """小机专用：当前市场窗口期 / 政策时机（强时效）。

    require_fresh=True + 本地过滤 < 2025。
    """
    return web_search(
        f"{query} 市场窗口 政策 时机 利率 展望 报告 {_AS_OF} 最新预测",
        max_results=4,
        require_fresh=True,
    )


def trend_research(query: str) -> list[dict]:
    """十年专用：站在当前看接下来 10 年——需要最新版的趋势统计与预测。

    require_fresh=True + 本地过滤 < 2025。
    我们要的不是"10 年前写的一份预测"，要"截至 {_AS_OF} 的最新趋势报告"。
    """
    return web_search(
        f"{query} 长期趋势 10 年 统计 复利 增速 预测 {_AS_OF} 最新",
        max_results=4,
        require_fresh=True,
    )


def contrarian_search(query: str) -> list[dict]:
    """反对专用：最新反对意见 / 质疑 / 风险警示。

    require_fresh=False——反对方的"被打脸叙事"经常需要引用历史案例
    （例如"2007 美国买房共识顶峰 → 次年崩盘"），强制过滤会丢论据。
    """
    return web_search(
        f"{query} 质疑 反对 问题 风险 泡沫 批评 {_AS_OF} 最新 "
        f"(site:reddit.com OR site:zhihu.com OR site:bilibili.com)",
        max_results=4,
        require_fresh=False,
    )


def data_table(query: str) -> list[dict]:
    """老算专用：最新版结构化数据 / 对比表。

    require_fresh=True + 本地过滤 < 2025。
    老算依赖实时数据算期望值，老数据会算出错误结论。
    """
    return web_search(
        f"{query} 对比表 数据 统计 价格 指数 报告 {_AS_OF} 最新",
        max_results=4,
        require_fresh=True,
    )
