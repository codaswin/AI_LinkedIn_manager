from __future__ import annotations

import pytest
from app.tools import search_hackernews
from app.tools.http_utils import HTTPSourceError
from app.tools.registry import execute_tool
from app.tools.search_hackernews import SearchHackerNewsArgs, execute
from pydantic import ValidationError

ITEM_MATCH = {
    "id": 1,
    "title": "New agentic AI framework released",
    "url": "https://example.com/agentic-ai",
    "text": "",
    "by": "alice",
    "time": 1_700_000_000,
    "score": 120,
    "descendants": 30,
    "kids": [11, 12],
}
ITEM_NO_MATCH = {
    "id": 2,
    "title": "Recipe for sourdough bread",
    "url": "https://example.com/bread",
    "by": "bob",
    "time": 1_700_000_100,
    "score": 5,
    "descendants": 1,
}


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    search_hackernews._id_list_cache.clear()
    search_hackernews._item_cache.clear()
    yield
    search_hackernews._id_list_cache.clear()
    search_hackernews._item_cache.clear()


async def test_filters_by_query_and_returns_normalized_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_story_ids(client, story_type):
        return [1, 2]

    async def fake_fetch_items(client, ids):
        by_id = {1: ITEM_MATCH, 2: ITEM_NO_MATCH}
        return [by_id[i] for i in ids if i in by_id]

    monkeypatch.setattr(search_hackernews, "_get_story_ids", fake_get_story_ids)
    monkeypatch.setattr(search_hackernews, "_fetch_items", fake_fetch_items)

    result = await execute(SearchHackerNewsArgs(query="agentic AI", limit=10))

    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["source"] == "hackernews"
    assert item["title"] == "New agentic AI framework released"
    assert item["engagement"] == {"score": 120, "comments": 30}
    assert item["author"] == "alice"
    assert item["metadata"]["hn_id"] == 1


async def test_no_matches_returns_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_story_ids(client, story_type):
        return [2]

    async def fake_fetch_items(client, ids):
        return [ITEM_NO_MATCH]

    monkeypatch.setattr(search_hackernews, "_get_story_ids", fake_get_story_ids)
    monkeypatch.setattr(search_hackernews, "_fetch_items", fake_fetch_items)

    result = await execute(SearchHackerNewsArgs(query="agentic AI", limit=10))
    assert result["results"] == []


async def test_one_bad_item_does_not_sink_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_story_ids(client, story_type):
        return [1, 2]

    async def fake_get_item(client, item_id):
        if item_id == 2:
            raise HTTPSourceError("boom")
        return ITEM_MATCH

    monkeypatch.setattr(search_hackernews, "_get_story_ids", fake_get_story_ids)
    monkeypatch.setattr(search_hackernews, "_get_item", fake_get_item)

    result = await execute(SearchHackerNewsArgs(query="agentic AI", limit=10))
    assert len(result["results"]) == 1


async def test_upstream_timeout_surfaces_as_error_result_via_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_story_ids(client, story_type):
        raise HTTPSourceError("GET timed out after 3 attempts: TimeoutException")

    monkeypatch.setattr(search_hackernews, "_get_story_ids", fake_get_story_ids)

    from app.tools import registry as registry_module

    registry_module._import_all_tools()
    tool_result = await execute_tool("search_hackernews", {"query": "agentic AI", "limit": 5})
    assert tool_result["status"] == "error"
    assert "timed out" in tool_result["error"].lower()


async def test_malformed_item_missing_title_still_produces_a_usable_result(monkeypatch: pytest.MonkeyPatch) -> None:
    malformed = {
        "id": 3,
        "url": "https://example.com/agentic-ai-thing",
        "text": "a discussion about agentic AI thing",
        "score": 10,
        "time": 1_700_000_200,
    }

    async def fake_get_story_ids(client, story_type):
        return [3]

    async def fake_fetch_items(client, ids):
        return [malformed]

    monkeypatch.setattr(search_hackernews, "_get_story_ids", fake_get_story_ids)
    monkeypatch.setattr(search_hackernews, "_fetch_items", fake_fetch_items)

    result = await execute(SearchHackerNewsArgs(query="agentic AI thing", limit=10))
    assert result["results"][0]["title"] == "(untitled)"


async def test_include_comments_attaches_top_comment_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_story_ids(client, story_type):
        return [1]

    async def fake_fetch_items(client, ids):
        if ids == [1]:
            return [ITEM_MATCH]
        return [{"id": 11, "text": "Great find!"}, {"id": 12, "text": "Agreed, very agentic."}]

    monkeypatch.setattr(search_hackernews, "_get_story_ids", fake_get_story_ids)
    monkeypatch.setattr(search_hackernews, "_fetch_items", fake_fetch_items)

    result = await execute(SearchHackerNewsArgs(query="agentic AI", limit=10, include_comments=True))
    assert result["results"][0]["metadata"]["top_comments"] == ["Great find!", "Agreed, very agentic."]


def test_query_is_required() -> None:
    with pytest.raises(ValidationError):
        SearchHackerNewsArgs(query="")


@pytest.mark.parametrize("story_type", ["top", "new", "best", "show", "ask", "job"])
async def test_all_six_story_types_are_accepted_and_fetch_the_right_list(
    monkeypatch: pytest.MonkeyPatch, story_type: str
) -> None:
    requested_urls = []

    async def fake_get_story_ids(client, requested_story_type):
        requested_urls.append(requested_story_type)
        return [1]

    async def fake_fetch_items(client, ids):
        return [ITEM_MATCH]

    monkeypatch.setattr(search_hackernews, "_get_story_ids", fake_get_story_ids)
    monkeypatch.setattr(search_hackernews, "_fetch_items", fake_fetch_items)

    result = await execute(SearchHackerNewsArgs(query="agentic AI", story_type=story_type, limit=10))
    assert requested_urls == [story_type]
    assert len(result["results"]) == 1
