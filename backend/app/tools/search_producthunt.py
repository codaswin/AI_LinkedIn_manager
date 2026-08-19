"""Product Hunt research source — API v2 (GraphQL).

Product Hunt's GraphQL API has no free-text search field on `posts`, so this
fetches a bounded window of the newest posts and filters client-side by
query keywords — the same "fetch a bounded candidate window, then filter"
shape as search_hackernews.py, for the same reason (avoid pulling far more
data than a research run needs).

PRODUCTHUNT_TOKEN is required; missing it raises ProductHuntConfigError,
caught by tools.sandbox and turned into a normal error result so a missing
PH token degrades only this one source (PRP: "Keep Product Hunt failures
isolated so the entire researcher does not fail when this integration is
unavailable").
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

GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"

_CANDIDATE_WINDOW = 30

_POSTS_QUERY = """
query RecentPosts($first: Int!) {
  posts(first: $first, order: NEWEST) {
    edges {
      node {
        id
        name
        tagline
        description
        url
        website
        votesCount
        createdAt
        topics(first: 5) {
          edges { node { name } }
        }
      }
    }
  }
}
"""


class ProductHuntConfigError(RuntimeError):
    """Raised when PRODUCTHUNT_TOKEN is not set."""


class SearchProductHuntArgs(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=20)


def _require_token() -> str:
    token = resolve_credential("PRODUCTHUNT_TOKEN")
    if not token:
        raise ProductHuntConfigError("PRODUCTHUNT_TOKEN is not set. Set it on the Connections page before using search_producthunt.")
    return token


def _keywords(text: str) -> set[str]:
    return {tok.lower() for tok in text.split() if tok}


def _matches_query(node: dict[str, t.Any], query_keywords: set[str]) -> bool:
    haystack = f"{node.get('name', '')} {node.get('tagline', '')} {node.get('description', '')}".lower()
    return any(kw in haystack for kw in query_keywords)


def _to_result(node: dict[str, t.Any]) -> ResearchResult:
    published_at = None
    if node.get("createdAt"):
        try:
            published_at = datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00"))
        except ValueError:
            published_at = None

    topic_edges = ((node.get("topics") or {}).get("edges")) or []
    topics = [e["node"]["name"] for e in topic_edges if isinstance(e, dict) and e.get("node")]

    return ResearchResult(
        source="producthunt",
        title=node.get("name") or "(untitled)",
        url=node.get("url") or node.get("website") or "",
        content=node.get("tagline") or node.get("description") or "",
        published_at=published_at,
        engagement={"votes": node.get("votesCount", 0) or 0},
        metadata={"topics": topics, "website": node.get("website")},
    )


@registry.register(
    ToolDefinition(
        name="search_producthunt",
        description="Discover recently launched products on Product Hunt matching a query (GraphQL API v2, read-only)",
        requires_approval=False,
        timeout_seconds=15,
    ),
    schema=SearchProductHuntArgs,
)
async def execute(args: SearchProductHuntArgs) -> dict[str, t.Any]:
    token = _require_token()
    query_keywords = _keywords(args.query)

    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await request_with_retry(
            client,
            "POST",
            GRAPHQL_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"query": _POSTS_QUERY, "variables": {"first": _CANDIDATE_WINDOW}},
        )
    payload = response.json()
    if "errors" in payload:
        raise ProductHuntConfigError(f"Product Hunt GraphQL API returned errors: {payload['errors']}")

    edges = ((payload.get("data") or {}).get("posts") or {}).get("edges") or []
    nodes = [e["node"] for e in edges if isinstance(e, dict) and e.get("node")]
    matched = [n for n in nodes if _matches_query(n, query_keywords)]
    matched.sort(key=lambda n: n.get("votesCount", 0) or 0, reverse=True)
    matched = matched[: args.limit]

    return {"results": [_to_result(n).model_dump(mode="json") for n in matched]}
