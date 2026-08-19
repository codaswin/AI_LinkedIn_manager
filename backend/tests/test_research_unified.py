from __future__ import annotations

from typing import Any

import pytest
from app.agents import research_pipeline, research_sources
from app.agents.research_pipeline import UNIFIED_DEFAULT_SOURCES, research
from app.agents.research_sources import (
    research_github,
    research_hackernews,
    research_producthunt,
    research_reddit,
    research_rss,
)
from app.tenancy import context as tenancy_context


@pytest.fixture(autouse=True)
def _tenancy_context():
    token = tenancy_context.set_current_user_id("user-research-unified-test")
    yield
    tenancy_context.reset_current_user_id(token)


def _fake_execute_tool(tool_name: str):
    async def _fn(name: str, args: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        return {
            "status": "success",
            "result": {
                "results": [
                    {
                        "source": tool_name.replace("search_", ""),
                        "title": f"{tool_name} finding",
                        "url": f"https://example.com/{tool_name}",
                        "content": "agentic AI news",
                    }
                ]
            },
        }

    return _fn


@pytest.mark.parametrize(
    "wrapper,tool_name",
    [
        (research_hackernews, "search_hackernews"),
        (research_github, "search_github"),
        (research_producthunt, "search_producthunt"),
        (research_reddit, "search_reddit"),
    ],
)
async def test_named_wrapper_defaults_to_limit_20(monkeypatch: pytest.MonkeyPatch, wrapper, tool_name) -> None:
    captured = {}

    async def fake_execute_tool(name: str, args: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        captured["args"] = args
        return {"status": "success", "result": {"results": []}}

    monkeypatch.setattr(research_sources, "execute_tool", fake_execute_tool)
    await wrapper("agentic AI")
    assert captured["args"]["limit"] == 20


async def test_research_rss_wrapper_passes_explicit_feeds(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def fake_execute_tool(name: str, args: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        captured["args"] = args
        return {"status": "success", "result": {"results": []}}

    monkeypatch.setattr(research_sources, "execute_tool", fake_execute_tool)
    await research_rss("agentic AI", feeds=["https://example.com/rss.xml"])
    assert captured["args"]["feed_urls"] == ["https://example.com/rss.xml"]
    assert captured["args"]["limit"] == 20


def test_unified_default_sources_are_the_five_requested() -> None:
    assert UNIFIED_DEFAULT_SOURCES == ["hackernews", "github", "producthunt", "rss", "reddit"]


async def test_research_returns_normalized_flat_dicts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_github(query: str, limit: int) -> list:
        from app.agents.research_schema import ResearchResult

        return [
            ResearchResult(
                source="github",
                title="agentic-framework",
                url="https://github.com/x/agentic-framework",
                content="An agentic AI framework",
                author="someone",
                engagement={"stars": 42},
            )
        ]

    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "github", fake_github)

    results = await research("agentic AI framework", sources=["github"], limit_per_source=5)

    assert len(results) == 1
    item = results[0]
    assert set(item.keys()) == {"source", "title", "summary", "url", "author", "published_at", "score", "metadata"}
    assert item["source"] == "github"
    assert item["summary"] == "An agentic AI framework"
    assert item["score"] == 42


async def test_research_isolates_a_failing_source(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_github(query: str, limit: int) -> list:
        raise RuntimeError("github down")

    async def fake_hackernews(query: str, limit: int) -> list:
        from app.agents.research_schema import ResearchResult

        return [ResearchResult(source="hackernews", title="still works", url="https://example.com/hn")]

    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "github", fake_github)
    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "hackernews", fake_hackernews)

    results = await research("agentic AI", sources=["github", "hackernews"])
    assert len(results) == 1
    assert results[0]["source"] == "hackernews"


async def test_research_runs_sources_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    calls: list[str] = []

    async def make_source(name: str):
        async def _fn(query: str, limit: int) -> list:
            calls.append(f"{name}-start")
            await asyncio.sleep(0.05)
            calls.append(f"{name}-end")
            return []

        return _fn

    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "github", await make_source("github"))
    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "hackernews", await make_source("hackernews"))

    await research("agentic AI", sources=["github", "hackernews"])
    # If sequential, order would be [a-start, a-end, b-start, b-end]. Concurrent
    # execution means both "start" events land before either "end" event.
    assert calls.index("github-start") < calls.index("hackernews-end")
    assert calls.index("hackernews-start") < calls.index("github-end")


async def test_research_rejects_unknown_source() -> None:
    with pytest.raises(ValueError, match="Unknown research source"):
        await research("agentic AI", sources=["not-a-real-source"])


async def test_research_default_mutable_argument_is_not_shared_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_empty(query: str, limit: int) -> list:
        return []

    for name in UNIFIED_DEFAULT_SOURCES:
        monkeypatch.setitem(research_pipeline.ALL_SOURCES, name, fake_empty)

    first = await research("agentic AI")
    second = await research("agentic AI")
    assert first == second == []
    assert UNIFIED_DEFAULT_SOURCES == ["hackernews", "github", "producthunt", "rss", "reddit"]
