from __future__ import annotations

import pytest
from app.tools import search_reddit
from app.tools.registry import execute_tool
from app.tools.search_reddit import RedditConfigError, SearchRedditArgs, execute

POST = {
    "id": "abc123",
    "title": "New agentic AI framework for local LLMs",
    "selftext": "Check it out",
    "score": 42,
    "num_comments": 7,
    "permalink": "/r/LocalLLaMA/comments/abc123/new_agentic_ai_framework/",
    "created_utc": 1_700_000_000,
    "author": "someuser",
    "subreddit": "LocalLLaMA",
}


@pytest.fixture(autouse=True)
def _clear_token_cache() -> None:
    search_reddit._token_cache.clear()
    yield
    search_reddit._token_cache.clear()


@pytest.fixture(autouse=True)
def _reddit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("REDDIT_USER_AGENT", "test-agent/1.0")


async def test_successful_search_returns_normalized_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_access_token(client):
        return "tok"

    async def fake_search_one(client, token, user_agent, query, subreddit, sort, limit):
        assert token == "tok"
        return [POST]

    monkeypatch.setattr(search_reddit, "_get_access_token", fake_get_access_token)
    monkeypatch.setattr(search_reddit, "_search_one", fake_search_one)

    result = await execute(SearchRedditArgs(query="agentic AI", limit=10))

    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["source"] == "reddit"
    assert item["title"] == "New agentic AI framework for local LLMs"
    assert item["url"] == "https://reddit.com/r/LocalLLaMA/comments/abc123/new_agentic_ai_framework/"
    assert item["engagement"] == {"score": 42, "comments": 7}
    assert item["metadata"]["subreddit"] == "LocalLLaMA"


async def test_missing_credentials_raise_config_error_and_sandbox_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)

    from app.tools import registry as registry_module

    registry_module._import_all_tools()
    tool_result = await execute_tool("search_reddit", {"query": "agentic AI"})
    assert tool_result["status"] == "error"
    assert "REDDIT_CLIENT_ID" in tool_result["error"]


async def test_missing_credentials_raises_directly() -> None:
    import os

    old = os.environ.pop("REDDIT_CLIENT_SECRET", None)
    try:
        with pytest.raises(RedditConfigError, match="REDDIT_CLIENT_SECRET"):
            await execute(SearchRedditArgs(query="agentic AI"))
    finally:
        if old is not None:
            os.environ["REDDIT_CLIENT_SECRET"] = old


async def test_dedupes_across_multiple_subreddits(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_access_token(client):
        return "tok"

    async def fake_search_one(client, token, user_agent, query, subreddit, sort, limit):
        return [POST]  # same post "found" in both subreddits

    monkeypatch.setattr(search_reddit, "_get_access_token", fake_get_access_token)
    monkeypatch.setattr(search_reddit, "_search_one", fake_search_one)

    result = await execute(SearchRedditArgs(query="agentic AI", subreddits=["LocalLLaMA", "MachineLearning"], limit=10))
    assert len(result["results"]) == 1


async def test_one_subreddit_failing_does_not_sink_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_access_token(client):
        return "tok"

    async def fake_search_one(client, token, user_agent, query, subreddit, sort, limit):
        if subreddit == "MachineLearning":
            raise RuntimeError("boom")
        return [POST]

    monkeypatch.setattr(search_reddit, "_get_access_token", fake_get_access_token)
    monkeypatch.setattr(search_reddit, "_search_one", fake_search_one)

    result = await execute(SearchRedditArgs(query="agentic AI", subreddits=["LocalLLaMA", "MachineLearning"], limit=10))
    assert len(result["results"]) == 1


async def test_token_response_missing_access_token_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class _FakeResponse:
        def json(self) -> dict:
            return {"error": "invalid_grant"}

        def raise_for_status(self) -> None:
            return None

        status_code = 200

    async def fake_request_with_retry(client, method, url, **kwargs):
        return _FakeResponse()

    monkeypatch.setattr(search_reddit, "request_with_retry", fake_request_with_retry)

    async with httpx.AsyncClient() as client:
        with pytest.raises(RedditConfigError, match="missing access_token"):
            await search_reddit._fetch_access_token(client)
