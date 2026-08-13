from __future__ import annotations

import pytest
from app.tools import search_rss
from app.tools.registry import execute_tool
from app.tools.search_rss import SearchRSSArgs, execute

RSS_XML_MATCH = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Example AI Blog</title>
<item>
  <title>Announcing our new agentic AI framework</title>
  <link>https://blog.example.com/agentic-ai-framework</link>
  <description>We are excited to announce agentic AI tooling.</description>
  <author>team@example.com</author>
  <pubDate>Mon, 01 Aug 2026 12:00:00 GMT</pubDate>
</item>
</channel></rss>
"""

RSS_XML_NO_MATCH = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Cooking Blog</title>
<item>
  <title>Best sourdough recipes</title>
  <link>https://blog.example.com/sourdough</link>
  <description>How to bake bread.</description>
  <pubDate>Tue, 02 Aug 2026 12:00:00 GMT</pubDate>
</item>
</channel></rss>
"""


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


@pytest.fixture(autouse=True)
def _rss_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RSS_FEEDS", "https://blog.example.com/rss.xml,https://cooking.example.com/rss.xml")


async def test_filters_by_query_across_configured_feeds(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request_with_retry(client, method, url, **kwargs):
        if "cooking" in url:
            return _FakeResponse(RSS_XML_NO_MATCH)
        return _FakeResponse(RSS_XML_MATCH)

    monkeypatch.setattr(search_rss, "request_with_retry", fake_request_with_retry)

    result = await execute(SearchRSSArgs(query="agentic AI", limit=10))

    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["source"] == "rss"
    assert item["title"] == "Announcing our new agentic AI framework"
    assert item["url"] == "https://blog.example.com/agentic-ai-framework"
    assert item["metadata"]["feed_title"] == "Example AI Blog"
    assert item["published_at"] is not None


async def test_no_query_match_falls_back_to_most_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request_with_retry(client, method, url, **kwargs):
        return _FakeResponse(RSS_XML_NO_MATCH)

    monkeypatch.setattr(search_rss, "request_with_retry", fake_request_with_retry)

    result = await execute(SearchRSSArgs(query="agentic AI", limit=10))
    assert len(result["results"]) >= 1
    assert result["results"][0]["title"] == "Best sourdough recipes"


def test_default_feed_list_is_the_eight_requested_feeds() -> None:
    assert search_rss.DEFAULT_RSS_FEEDS == [
        "https://techcrunch.com/feed/",
        "https://venturebeat.com/feed/",
        "https://hnrss.org/best",
        "https://hnrss.org/newest",
        "https://www.ycombinator.com/blog/rss",
        "https://www.technologyreview.com/feed/",
        "https://blog.google/technology/ai/rss/",
        "https://huggingface.co/blog/feed.xml",
    ]


async def test_no_rss_feeds_env_falls_back_to_default_feeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RSS_FEEDS", raising=False)
    requested_urls = []

    async def fake_request_with_retry(client, method, url, **kwargs):
        requested_urls.append(url)
        return _FakeResponse(RSS_XML_NO_MATCH)

    monkeypatch.setattr(search_rss, "request_with_retry", fake_request_with_retry)

    await execute(SearchRSSArgs(query="agentic AI", limit=10))
    assert set(requested_urls) == set(search_rss.DEFAULT_RSS_FEEDS)


async def test_explicit_feed_urls_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_request_with_retry(client, method, url, **kwargs):
        calls.append(url)
        return _FakeResponse(RSS_XML_MATCH)

    monkeypatch.setattr(search_rss, "request_with_retry", fake_request_with_retry)

    await execute(SearchRSSArgs(query="agentic AI", feed_urls=["https://override.example.com/rss.xml"], limit=10))
    assert calls == ["https://override.example.com/rss.xml"]


async def test_one_broken_feed_does_not_sink_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tools.http_utils import HTTPSourceError

    async def fake_request_with_retry(client, method, url, **kwargs):
        if "cooking" in url:
            raise HTTPSourceError("boom")
        return _FakeResponse(RSS_XML_MATCH)

    monkeypatch.setattr(search_rss, "request_with_retry", fake_request_with_retry)

    result = await execute(SearchRSSArgs(query="agentic AI", limit=10))
    assert len(result["results"]) == 1


async def test_malformed_xml_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request_with_retry(client, method, url, **kwargs):
        return _FakeResponse("not xml at all <<<")

    monkeypatch.setattr(search_rss, "request_with_retry", fake_request_with_retry)

    result = await execute(SearchRSSArgs(query="agentic AI", limit=10))
    assert result["results"] == []


async def test_sandboxed_execution_via_execute_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tools.http_utils import HTTPSourceError

    async def fake_request_with_retry(client, method, url, **kwargs):
        # Simulates a test/CI environment with no real network access: every
        # default feed fails, and that must still surface as a clean
        # success-with-zero-results, never a sandbox-level error.
        raise HTTPSourceError("no network in test environment")

    monkeypatch.setattr(search_rss, "request_with_retry", fake_request_with_retry)

    from app.tools import registry as registry_module

    registry_module._import_all_tools()
    tool_result = await execute_tool("search_rss", {"query": "agentic AI"})
    assert tool_result["status"] == "success"
    assert tool_result["result"]["results"] == []
