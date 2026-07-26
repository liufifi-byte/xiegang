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
