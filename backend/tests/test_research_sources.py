from __future__ import annotations

import pytest
from app.agents import research_sources
from app.agents.research_schema import ResearchResult
from app.agents.research_sources import (
    ALL_SOURCES,
    DEFAULT_SOURCES,
    OPTIONAL_SOURCES,
    fetch_github,
    fetch_hackernews,
    fetch_producthunt,
    fetch_reddit,
    fetch_rss,
    fetch_web,
    fetch_x,
)


def test_x_is_optional_only_never_a_default_source() -> None:
    assert "x" not in DEFAULT_SOURCES
    assert "x" in OPTIONAL_SOURCES
    assert "x" in ALL_SOURCES


def test_default_sources_are_exactly_the_six_free_sources() -> None:
    assert set(DEFAULT_SOURCES) == {"hackernews", "reddit", "web", "github", "producthunt", "rss"}


@pytest.mark.parametrize(
    "adapter,tool_name",
    [
        (fetch_hackernews, "search_hackernews"),
        (fetch_web, "search_web"),
        (fetch_github, "search_github"),
        (fetch_producthunt, "search_producthunt"),
        (fetch_rss, "search_rss"),
    ],
)
async def test_adapter_extracts_successful_results(monkeypatch: pytest.MonkeyPatch, adapter, tool_name) -> None:
    raw_result = ResearchResult(source=tool_name, title="A finding", url="https://example.com/a").model_dump(mode="json")

    async def fake_execute_tool(name, args, approved=False):
        assert name == tool_name
        return {"status": "success", "result": {"results": [raw_result]}}

    monkeypatch.setattr(research_sources, "execute_tool", fake_execute_tool)

    results = await adapter("agentic AI", 5)
    assert len(results) == 1
    assert results[0].title == "A finding"


@pytest.mark.parametrize("tool_name", ["search_hackernews", "search_reddit", "search_web", "search_github", "search_producthunt", "search_rss"])
async def test_error_status_yields_empty_list_not_an_exception(monkeypatch: pytest.MonkeyPatch, tool_name) -> None:
    adapter_by_tool = {
        "search_hackernews": fetch_hackernews,
        "search_reddit": fetch_reddit,
        "search_web": fetch_web,
        "search_github": fetch_github,
        "search_producthunt": fetch_producthunt,
        "search_rss": fetch_rss,
    }

    async def fake_execute_tool(name, args, approved=False):
        return {"status": "error", "error": "upstream unavailable"}

    monkeypatch.setattr(research_sources, "execute_tool", fake_execute_tool)

    results = await adapter_by_tool[tool_name]("agentic AI", 5)
    assert results == []


async def test_malformed_result_item_is_skipped_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute_tool(name, args, approved=False):
        return {"status": "success", "result": {"results": [{"not": "a valid ResearchResult"}]}}

    monkeypatch.setattr(research_sources, "execute_tool", fake_execute_tool)

    results = await fetch_hackernews("agentic AI", 5)
    assert results == []


async def test_reddit_adapter_infers_subreddits_from_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def fake_execute_tool(name, args, approved=False):
        captured["args"] = args
        return {"status": "success", "result": {"results": []}}

    monkeypatch.setattr(research_sources, "execute_tool", fake_execute_tool)

    await fetch_reddit("startup fundraising advice", 5)
    assert captured["args"]["subreddits"] == ["startups", "SaaS", "Entrepreneur"]


async def test_reddit_adapter_explicit_subreddits_override_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def fake_execute_tool(name, args, approved=False):
        captured["args"] = args
        return {"status": "success", "result": {"results": []}}

    monkeypatch.setattr(research_sources, "execute_tool", fake_execute_tool)

    await fetch_reddit("startup fundraising advice", 5, subreddits=["webdev"])
    assert captured["args"]["subreddits"] == ["webdev"]


async def test_reddit_adapter_falls_back_to_sitewide_search_when_no_hint_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def fake_execute_tool(name, args, approved=False):
        captured["args"] = args
        return {"status": "success", "result": {"results": []}}

    monkeypatch.setattr(research_sources, "execute_tool", fake_execute_tool)

    await fetch_reddit("something totally unrelated to any hint", 5)
    assert captured["args"]["subreddits"] is None


async def test_fetch_x_normalizes_raw_composio_post_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute_tool(name, args, approved=False):
        assert name == "search_x_posts"
        return {
            "status": "success",
            "result": {
                "posts": [
                    {
                        "id": "123",
                        "text": "New agentic AI tool dropped today",
                        "author": {"username": "someuser"},
                        "like_count": 10,
                        "retweet_count": 2,
                        "created_at": "2026-08-01T00:00:00Z",
                    }
                ]
            },
        }

    monkeypatch.setattr(research_sources, "execute_tool", fake_execute_tool)

    results = await fetch_x("agentic AI", 5)
    assert len(results) == 1
    assert results[0].source == "x"
    assert results[0].author == "someuser"
    assert results[0].engagement == {"likes": 10, "reposts": 2}


async def test_fetch_x_skips_posts_with_no_derivable_url(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute_tool(name, args, approved=False):
        return {"status": "success", "result": {"posts": [{"text": "no id or url here"}]}}

    monkeypatch.setattr(research_sources, "execute_tool", fake_execute_tool)

    results = await fetch_x("agentic AI", 5)
    assert results == []
