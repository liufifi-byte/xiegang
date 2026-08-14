import json
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from app.tools import web_search as module


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def test_glm_web_search_returns_structured_openable_sources(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            glm_base_url="https://open.bigmodel.cn/api/paas/v4",
            glm_api_key="secret-key",
            web_search_engine="search_pro",
            web_search_timeout_seconds=15,
        ),
    )

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "search_result": [
                    {
                        "title": "官方利率说明",
                        "link": "https://example.com/rate",
                        "content": "当前贷款利率说明。",
                        "media": "官方机构",
                        "publish_date": "2026-07-01",
                    },
                    {
                        "title": "没有链接的结果",
                        "content": "不应进入证据池。",
                    },
                ]
            }
        )

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    results = module.web_search("查询最新房贷利率", 5)

    assert captured["url"].endswith("/web_search")
    assert captured["payload"]["search_engine"] == "search_pro"
    assert captured["payload"]["count"] == 5
    assert captured["headers"]["Authorization"] == "Bearer secret-key"
    assert results == [
        {
            "title": "官方利率说明",
            "url": "https://example.com/rate",
            "content": "当前贷款利率说明。",
            "score": 0.92,
            "media": "官方机构",
            "publish_date": "2026-07-01",
            # P0: 信源分级字段（example.com 不在 trusted/blog 白名单 → media）
            "source_grade": "media",
        }
    ]


def test_glm_web_search_uses_stable_public_error(monkeypatch):
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            glm_base_url="https://open.bigmodel.cn/api/paas/v4",
            glm_api_key="secret-key",
            web_search_engine="search_pro",
            web_search_timeout_seconds=15,
        ),
    )
    monkeypatch.setattr(
        module,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("network")),
    )
    with pytest.raises(module.WebSearchError, match="independent web search"):
        module.web_search("test")


# ============================================================
# 时效过滤测试：require_fresh=True 时本地丢早于阈值年份的旧条
# ============================================================

def _fake_settings_with_year(min_year):
    """构造带 web_search_min_publish_year 的 fake settings。"""
    return SimpleNamespace(
        glm_base_url="https://open.bigmodel.cn/api/paas/v4",
        glm_api_key="secret-key",
        web_search_engine="search_pro",
        web_search_timeout_seconds=15,
        web_search_min_publish_year=min_year,
    )


def _make_urlopen_returning(results):
    """构造一个 fake urlopen，固定返回给定 search_result list。"""

    def fake_urlopen(request, timeout):
        return FakeResponse({"search_result": results})

    return fake_urlopen


def test_require_fresh_filters_stale_results(monkeypatch):
    """require_fresh=True：早于阈值的 publish_date 被丢弃。"""
    monkeypatch.setattr(module, "get_settings",
                        lambda: _fake_settings_with_year(2025))
    monkeypatch.setattr(module, "urlopen", _make_urlopen_returning([
        # 4 条结构完整的样本：1 新 + 2 旧 + 1 无日期
        {"title": "2026 新文", "link": "https://a.com/1",
         "content": "新内容", "publish_date": "2026-06-01"},
        {"title": "2024 旧文", "link": "https://b.com/2",
         "content": "旧内容", "publish_date": "2024-12-01"},  # 应被丢
        {"title": "2023 旧文", "link": "https://c.com/3",
         "content": "更旧内容", "publish_date": "2023-01-15"},  # 应被丢
        {"title": "无日期文", "link": "https://d.com/4",
         "content": "无日期", "publish_date": None},  # 保留（兜底）
    ]))
    results = module.web_search("test", require_fresh=True)
    titles = [r["title"] for r in results]
    assert "2026 新文" in titles
    assert "无日期文" in titles  # 无日期 → 不被丢
    assert "2024 旧文" not in titles
    assert "2023 旧文" not in titles


def test_not_require_fresh_keeps_all_results(monkeypatch):
    """require_fresh=False（默认）：不过滤日期，保留全部。"""
    monkeypatch.setattr(module, "get_settings",
                        lambda: _fake_settings_with_year(2025))
    monkeypatch.setattr(module, "urlopen", _make_urlopen_returning([
        {"title": "2026 新文", "link": "https://a.com/1",
         "content": "新内容", "publish_date": "2026-06-01"},
        {"title": "2018 旧文", "link": "https://b.com/2",
         "content": "旧内容", "publish_date": "2018-01-01"},
    ]))
    results = module.web_search("test", require_fresh=False)
    titles = [r["title"] for r in results]
    assert "2026 新文" in titles
    assert "2018 旧文" in titles  # 历史案例正常保留


def test_throwable_threshold_configurable(monkeypatch):
    """web_search_min_publish_year 不同 → 阈值跟随。"""
    # 阈值设 2026：2025 的也会丢
    monkeypatch.setattr(module, "get_settings",
                        lambda: _fake_settings_with_year(2026))
    monkeypatch.setattr(module, "urlopen", _make_urlopen_returning([
        {"title": "2025 文", "link": "https://a.com/1",
         "content": "x", "publish_date": "2025-12-31"},
        {"title": "2026 文", "link": "https://b.com/2",
         "content": "y", "publish_date": "2026-01-01"},
    ]))
    results = module.web_search("test", require_fresh=True)
    titles = [r["title"] for r in results]
    assert "2026 文" in titles
    assert "2025 文" not in titles


def test_parse_publish_year_handles_chinese_format():
    """publish_date "2026年7月31日" 等中文格式应被正确识别。"""
    assert module._parse_publish_year("2026-07-31") == 2026
    assert module._parse_publish_year("2026年7月31日") == 2026
    assert module._parse_publish_year("2026/7/31") == 2026
    assert module._parse_publish_year(None) is None
    assert module._parse_publish_year("") is None
    assert module._parse_publish_year("not-a-date") is None


def test_temp_field_cleaned_from_output(monkeypatch):
    """_publish_year 临时字段不应出现在最终返回结果里。"""
    monkeypatch.setattr(module, "get_settings",
                        lambda: _fake_settings_with_year(2025))
    monkeypatch.setattr(module, "urlopen", _make_urlopen_returning([
        {"title": "新文", "link": "https://a.com/1",
         "content": "x", "publish_date": "2026-06-01"},
    ]))
    results = module.web_search("test", require_fresh=True)
    assert len(results) == 1
    assert "_publish_year" not in results[0]
