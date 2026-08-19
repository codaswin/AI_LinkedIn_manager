from __future__ import annotations

import httpx
import pytest
from app.tenancy import context as tenancy_context
from app.tenancy import credentials as tenancy_credentials
from app.tools import search_github
from app.tools.http_utils import HTTPSourceError
from app.tools.registry import execute_tool
from app.tools.search_github import SearchGitHubArgs, execute

_USER = "search-github-test-user"


@pytest.fixture(autouse=True)
def _tenancy_context() -> None:
    tenancy_credentials.clear_user(_USER)
    token = tenancy_context.set_current_user_id(_USER)
    yield
    tenancy_context.reset_current_user_id(token)
    tenancy_credentials.clear_user(_USER)


REPO = {
    "full_name": "someorg/agentic-framework",
    "html_url": "https://github.com/someorg/agentic-framework",
    "description": "A new agentic AI framework",
    "stargazers_count": 1500,
    "forks_count": 80,
    "language": "Python",
    "topics": ["agentic-ai", "llm"],
    "updated_at": "2026-08-01T12:00:00Z",
    "owner": {"login": "someorg"},
}


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


async def test_successful_search_returns_normalized_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request_with_retry(client, method, url, **kwargs):
        assert kwargs["params"]["q"] == "agentic AI"
        return _FakeResponse({"items": [REPO]})

    monkeypatch.setattr(search_github, "request_with_retry", fake_request_with_retry)

    result = await execute(SearchGitHubArgs(query="agentic AI", limit=10))

    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["source"] == "github"
    assert item["title"] == "someorg/agentic-framework"
    assert item["url"] == "https://github.com/someorg/agentic-framework"
    assert item["engagement"] == {"stars": 1500, "forks": 80}
    assert item["author"] == "someorg"
    assert item["metadata"]["language"] == "Python"


async def test_uses_github_token_header_when_set() -> None:
    tenancy_credentials.set_credential(_USER, "GITHUB_TOKEN", "ghp_test123")
    headers = search_github._headers()
    assert headers["Authorization"] == "Bearer ghp_test123"


async def test_works_unauthenticated_when_no_token_set() -> None:
    headers = search_github._headers()
    assert "Authorization" not in headers


async def test_api_error_surfaces_as_error_result_via_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request_with_retry(client, method, url, **kwargs):
        raise HTTPSourceError("GET failed after 3 attempts: 403 rate limited")

    monkeypatch.setattr(search_github, "request_with_retry", fake_request_with_retry)

    from app.tools import registry as registry_module

    registry_module._import_all_tools()
    tool_result = await execute_tool("search_github", {"query": "agentic AI"})
    assert tool_result["status"] == "error"


async def test_malformed_response_missing_items_key_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request_with_retry(client, method, url, **kwargs):
        return _FakeResponse({})

    monkeypatch.setattr(search_github, "request_with_retry", fake_request_with_retry)

    result = await execute(SearchGitHubArgs(query="agentic AI", limit=10))
    assert result["results"] == []


async def test_timeout_raises_http_source_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request(self, method, url, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    with pytest.raises(HTTPSourceError):
        await execute(SearchGitHubArgs(query="agentic AI", limit=10))
