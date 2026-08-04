"""议员专属工具：每个议员 1 个特色工具。

V1: 5 个全是 web_search 薄包装（只是 query 前缀不同）
V2-S3: 真差异化——每个工具有专属 query 模板
本次修订: 取消强制 require_fresh=True——GLM 中文垂直搜索召回本就稀少，
        受一年期过滤下从 ~3 条降到 0 条。改为依赖 query 本身携带年份来引导新鲜度，
        让结果集最大化。market_window_check（明显强时效）保留 require_fresh=True。
"""
from app.tools.web_search import web_search


def historical_risk_lookup(query: str) -> list[dict]:
    """老沉专用：历史下行 / 翻车案例 + 最新复盘。

    不再写死具体历史年份（旧版 "2008 2015 2018 2022" 会把搜索锁死在老文）。
    用"上轮周期 顶点 回撤 终结 复盘"跟随时事的措辞，单独靠 query 引导。
    历史案例本就需要老内容，不强制 require_fresh。
    """
    return web_search(
        f"{query} 历史上轮周期 顶点 回撤 翻盘 终结 复盘 最新",
        max_results=4,
        require_fresh=False,
    )


def market_window_check(query: str) -> list[dict]:
    """小机专用：当前市场窗口期 / 政策时机（强时效，保留 require_fresh）。"""
    return web_search(
        f"{query} 市场窗口 政策 时机 利率 展望 报告 2026 最新预测",
        max_results=4,
        require_fresh=True,
    )


def trend_research(query: str) -> list[dict]:
    """十年专用：长期趋势数据的最新版本。靠 query 引导，不强制 fresh。"""
    return web_search(
        f"{query} 长期趋势 10 年 统计 复利 增速 预测 2026 最新",
        max_results=4,
        require_fresh=False,
    )


def contrarian_search(query: str) -> list[dict]:
    """反对专用：最新反对意见 / 质疑 / 风险警示。靠 query 引导，不强制 fresh。"""
    return web_search(
        f"{query} 质疑 反对 问题 风险 泡沫 批评 2026 最新 "
        f"(site:reddit.com OR site:zhihu.com OR site:bilibili.com)",
        max_results=4,
        require_fresh=False,
    )


def data_table(query: str) -> list[dict]:
    """老算专用：最新版结构化数据 / 对比表。靠 query 引导，不强制 fresh。"""
    return web_search(
        f"{query} 对比表 数据 统计 价格 指数 报告 2026 最新",
        max_results=4,
        require_fresh=False,
    )
