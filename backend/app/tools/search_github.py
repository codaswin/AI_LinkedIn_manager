"""GitHub research source — official REST API, repository search.

Works unauthenticated (GitHub's public rate limit applies); set GITHUB_TOKEN
to raise that limit. Focused on repository discovery only (trending/new
open-source AI projects, agent frameworks, dev tools) per PRP scope — code
search is a separate, much more rate-limited GitHub endpoint and isn't
needed for that goal.
"""

from __future__ import annotations

import typing as t
from datetime import datetime

import httpx
from app.agents.research_schema import ResearchResult
from app.tenancy.credentials import resolve_credential
from app.tools.http_utils import request_with_retry
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

BASE_URL = "https://api.github.com/search/repositories"


class SearchGitHubArgs(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=30)
    sort: t.Literal["stars", "forks", "updated"] = "stars"


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ai-linkedin-manager-research-agent"}
    token = resolve_credential("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _to_result(repo: dict[str, t.Any]) -> ResearchResult:
    published_at = None
    if repo.get("updated_at"):
        try:
            published_at = datetime.fromisoformat(repo["updated_at"].replace("Z", "+00:00"))
        except ValueError:
            published_at = None

    return ResearchResult(
        source="github",
        title=repo.get("full_name") or repo.get("name") or "(untitled)",
        url=repo.get("html_url") or "",
        content=repo.get("description") or "",
        author=(repo.get("owner") or {}).get("login"),
        published_at=published_at,
        engagement={"stars": repo.get("stargazers_count", 0) or 0, "forks": repo.get("forks_count", 0) or 0},
        metadata={
            "language": repo.get("language"),
            "topics": repo.get("topics") or [],
            "updated_at": repo.get("updated_at"),
        },
    )


@registry.register(
    ToolDefinition(
        name="search_github",
        description="Search GitHub repositories via the official REST API (read-only, works unauthenticated)",
        requires_approval=False,
        timeout_seconds=15,
    ),
    schema=SearchGitHubArgs,
)
async def execute(args: SearchGitHubArgs) -> dict[str, t.Any]:
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await request_with_retry(
            client,
            "GET",
            BASE_URL,
            headers=_headers(),
            params={"q": args.query, "sort": args.sort, "order": "desc", "per_page": args.limit},
        )
    payload = response.json()
    items = payload.get("items") or []
    return {"results": [_to_result(repo).model_dump(mode="json") for repo in items[: args.limit]]}
