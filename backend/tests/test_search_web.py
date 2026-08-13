from __future__ import annotations

import pytest
from app.tools import search_web, web_search_provider
from app.tools.search_web import SearchWebArgs, execute
from app.tools.web_search_provider import (
    BraveWebSearchProvider,
    DuckDuckGoWebSearchProvider,
    NullWebSearchProvider,
    WebSearchProvider,
    get_default_provider,
)


class _FakeProvider(WebSearchProvider):
    def __init__(self, results):
        self._results = results

    async def search(self, query, limit):
        return self._results


async def test_execute_maps_provider_results_to_research_results(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_results = [
        {
            "title": "Agentic AI tooling roundup",
            "url": "https://example.com/roundup",
            "snippet": "A roundup of agentic AI tools",
            "domain": "example.com",
            "published_at": "2026-08-01T00:00:00Z",
        }
    ]
    monkeypatch.setattr(search_web, "get_default_provider", lambda: _FakeProvider(fake_results))

    result = await execute(SearchWebArgs(query="agentic AI tools", limit=10))

    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["source"] == "web"
    assert item["title"] == "Agentic AI tooling roundup"
    assert item["metadata"]["domain"] == "example.com"
    assert item["published_at"].startswith("2026-08-01")


async def test_unparseable_published_at_is_left_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_results = [{"title": "X", "url": "https://example.com/x", "snippet": "y", "published_at": "2 days ago"}]
    monkeypatch.setattr(search_web, "get_default_provider", lambda: _FakeProvider(fake_results))

    result = await execute(SearchWebArgs(query="agentic AI", limit=10))
    assert result["results"][0]["published_at"] is None


async def test_null_provider_used_when_web_search_provider_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "none")
    provider = get_default_provider()
    assert isinstance(provider, NullWebSearchProvider)
    assert await provider.search("agentic AI", 10) == []


async def test_default_provider_is_duckduckgo_with_no_env_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    provider = get_default_provider()
    assert isinstance(provider, DuckDuckGoWebSearchProvider)


async def test_missing_brave_api_key_falls_back_to_null_provider_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "brave")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    provider = get_default_provider()
    assert isinstance(provider, NullWebSearchProvider)


async def test_brave_provider_selected_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "key123")
    provider = get_default_provider()
    assert isinstance(provider, BraveWebSearchProvider)


async def test_unknown_provider_name_falls_back_to_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "bogus-provider")
    provider = get_default_provider()
    assert isinstance(provider, NullWebSearchProvider)


async def test_brave_provider_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResponse:
        def json(self):
            return {
                "web": {
                    "results": [
                        {
                            "title": "Agentic AI news",
                            "url": "https://news.example.com/a",
                            "description": "News about agentic AI",
                            "meta_url": {"hostname": "news.example.com"},
                            "age": "1 day ago",
                        }
                    ]
                }
            }

    async def fake_request_with_retry(client, method, url, **kwargs):
        return _FakeResponse()

    monkeypatch.setattr(web_search_provider, "request_with_retry", fake_request_with_retry)

    provider = BraveWebSearchProvider(api_key="key123")
    results = await provider.search("agentic AI", 10)
    assert results[0]["title"] == "Agentic AI news"
    assert results[0]["domain"] == "news.example.com"


async def test_duckduckgo_provider_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def text(self, query, max_results):
            assert query == "agentic AI"
            assert max_results == 10
            return [
                {
                    "title": "Agentic AI news",
                    "href": "https://news.example.com/a",
                    "body": "News about agentic AI",
                }
            ]

    monkeypatch.setattr(web_search_provider, "DDGS", _FakeDDGS)

    provider = DuckDuckGoWebSearchProvider()
    results = await provider.search("agentic AI", 10)
    assert results[0]["title"] == "Agentic AI news"
    assert results[0]["url"] == "https://news.example.com/a"
    assert results[0]["domain"] == "news.example.com"
    assert results[0]["published_at"] is None


async def test_duckduckgo_provider_used_via_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_results = [{"title": "DDG result", "href": "https://example.com/ddg", "body": "snippet"}]

    class _FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def text(self, query, max_results):
            return fake_results

    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    monkeypatch.setattr(web_search_provider, "DDGS", _FakeDDGS)

    result = await execute(SearchWebArgs(query="agentic AI", limit=5))
    assert result["results"][0]["title"] == "DDG result"
