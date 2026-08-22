"""web_search 来源质量加固测试（2026-08-22 用户实测反馈驱动）。

三个新行为：
1. 正文中抠发布年份（自媒体把日期写进正文绕过字段的老洞）；
2. require_fresh 下无日期结果至多 1 条；
3. 自媒体（blog）封顶 2 条 + 权威感知排序（trusted > media > blog）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tools import web_search as ws


def test_year_from_content_patterns():
    f = ws._year_from_content
    assert f("前言 发布时间：2023-09-19 08:14:10 ↗ 当你开始") == 2023
    assert f("某某（发布时间：2021-04-03 09:26:00）正文") == 2021
    assert f("法院判决书（2025-04-06）显示") == 2025
    assert f("2024年6月的报道") == 2024
    assert f("") is None
    assert f("完全没有日期的内容") is None


def test_parse_publish_year_falls_back_to_content():
    assert ws._parse_publish_year(None, "发布时间：2022-11-30") == 2022
    assert ws._parse_publish_year("", "发表于 2020年5月") == 2020
    assert ws._parse_publish_year("2026-07-31", "发布时间：2023-01-01") == 2026  # 字段优先


def _run_with_raw(monkeypatch, raw_results, *, require_fresh=False,
                  min_year=2025):
    """绕过 HTTP：直接注入 provider 返回。"""
    import json

    class _FakeResp:
        def read(self):
            return json.dumps({"search_result": raw_results}).encode()

    class _FakeUrlopen:
        def __init__(self, request, timeout=None):
            pass

        def __enter__(self):
            return _FakeResp()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ws, "urlopen", _FakeUrlopen)
    from app.config import get_settings

    monkeypatch.setattr(
        get_settings(), "web_search_min_publish_year", min_year, raising=False
    )
    return ws.web_search("测试查询", max_results=5, require_fresh=require_fresh)


def test_undated_and_stale_capped_under_require_fresh(monkeypatch):
    raw = [
        # 老帖：日期只在正文里（用户实测翻车形态）→ 应被识别为 2021 并丢弃
        {"link": "https://blog.csdn.net/a", "content": "正文 发布时间：2021-04-03 09:26:00",
         "title": "老帖"},
        # 无日期帖 ×3 → 至多保留 1 条
        {"link": "https://zhihu.com/q/1", "content": "无日期内容甲"},
        {"link": "https://zhihu.com/q/2", "content": "无日期内容乙"},
        {"link": "https://zhihu.com/q/3", "content": "无日期内容丙"},
        # 新鲜权威帖 → 保留
        {"link": "https://www.gov.cn/x", "content": "2026 年新政策",
         "publish_date": "2026-03-01"},
    ]
    results = _run_with_raw(monkeypatch, raw, require_fresh=True)
    urls = [r["url"] for r in results]
    assert "https://blog.csdn.net/a" not in urls          # 正文抠到 2021 → 过期丢弃
    assert urls.count("https://www.gov.cn/x") == 1        # 新鲜权威保留
    undated = [u for u in urls if "zhihu.com" in u]
    assert len(undated) <= 1                              # 无日期封顶 1 条


def test_blog_capped_and_trusted_ranked_first(monkeypatch):
    raw = [
        {"link": "https://zhihu.com/q/1", "content": "自媒体一"},
        {"link": "https://blog.csdn.net/b", "content": "自媒体二"},
        {"link": "https://weibo.com/x", "content": "自媒体三"},
        {"link": "https://mp.weixin.qq.com/s/y", "content": "自媒体四"},
        {"link": "https://www.reuters.com/story", "content": "媒体",
         "publish_date": "2026-05-01"},
        {"link": "https://www.gov.cn/p", "content": "官方",
         "publish_date": "2026-05-02"},
    ]
    results = _run_with_raw(monkeypatch, raw)  # 不 require_fresh 也封顶
    blogs = [r for r in results if r["source_grade"] == "blog"]
    assert len(blogs) <= 2, [r["url"] for r in results]
    # 权威感知排序：trusted 排最前
    grades = [r["source_grade"] for r in results]
    assert grades[0] == "trusted"
    assert grades.index("trusted") < grades.index("blog")
