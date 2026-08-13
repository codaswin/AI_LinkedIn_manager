from __future__ import annotations

import pytest
from app.tools import search_producthunt
from app.tools.registry import execute_tool
from app.tools.search_producthunt import (
    ProductHuntConfigError,
    SearchProductHuntArgs,
    execute,
)

NODE_MATCH = {
    "id": "1",
    "name": "AgenticFlow",
    "tagline": "Build agentic AI workflows fast",
    "description": "A visual builder for agentic AI pipelines",
    "url": "https://www.producthunt.com/posts/agenticflow",
    "website": "https://agenticflow.example",
    "votesCount": 250,
    "createdAt": "2026-08-01T00:00:00Z",
    "topics": {"edges": [{"node": {"name": "Artificial Intelligence"}}]},
}
NODE_NO_MATCH = {
    "id": "2",
    "name": "RecipeBox",
    "tagline": "Organize your recipes",
    "description": "A recipe manager",
    "url": "https://www.producthunt.com/posts/recipebox",
    "votesCount": 10,
    "createdAt": "2026-08-01T00:00:00Z",
    "topics": {"edges": []},
}


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


@pytest.fixture(autouse=True)
def _ph_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRODUCTHUNT_TOKEN", "tok")


async def test_filters_by_query_and_returns_normalized_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request_with_retry(client, method, url, **kwargs):
        return _FakeResponse({"data": {"posts": {"edges": [{"node": NODE_MATCH}, {"node": NODE_NO_MATCH}]}}})

    monkeypatch.setattr(search_producthunt, "request_with_retry", fake_request_with_retry)

    result = await execute(SearchProductHuntArgs(query="agentic AI", limit=10))

    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["source"] == "producthunt"
    assert item["title"] == "AgenticFlow"
    assert item["engagement"] == {"votes": 250}
    assert item["metadata"]["topics"] == ["Artificial Intelligence"]


async def test_missing_token_raises_config_error_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRODUCTHUNT_TOKEN", raising=False)
    with pytest.raises(ProductHuntConfigError, match="PRODUCTHUNT_TOKEN"):
        await execute(SearchProductHuntArgs(query="agentic AI"))


async def test_missing_token_is_isolated_by_sandbox_as_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRODUCTHUNT_TOKEN", raising=False)

    from app.tools import registry as registry_module

    registry_module._import_all_tools()
    tool_result = await execute_tool("search_producthunt", {"query": "agentic AI"})
    assert tool_result["status"] == "error"
    assert "PRODUCTHUNT_TOKEN" in tool_result["error"]


async def test_graphql_errors_field_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request_with_retry(client, method, url, **kwargs):
        return _FakeResponse({"errors": [{"message": "invalid token"}]})

    monkeypatch.setattr(search_producthunt, "request_with_retry", fake_request_with_retry)

    with pytest.raises(ProductHuntConfigError, match="errors"):
        await execute(SearchProductHuntArgs(query="agentic AI"))


async def test_no_matches_returns_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request_with_retry(client, method, url, **kwargs):
        return _FakeResponse({"data": {"posts": {"edges": [{"node": NODE_NO_MATCH}]}}})

    monkeypatch.setattr(search_producthunt, "request_with_retry", fake_request_with_retry)

    result = await execute(SearchProductHuntArgs(query="agentic AI", limit=10))
    assert result["results"] == []
