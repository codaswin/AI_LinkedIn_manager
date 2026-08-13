"""Generic web-search research source. Delegates to a swappable

WebSearchProvider (see web_search_provider.py) rather than any single
vendor's API, so the Researcher Agent never depends on which provider is
configured.
"""

from __future__ import annotations

import typing as t
from datetime import datetime

from app.agents.research_schema import ResearchResult
from app.tools.registry import ToolDefinition, registry
from app.tools.web_search_provider import get_default_provider
from pydantic import BaseModel, Field


class SearchWebArgs(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=20)


def _parse_published_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        # Some providers (e.g. Brave's "age" field) return a human string
        # like "2 days ago" rather than a timestamp — not parseable, and not
        # worth guessing at, so published_at is left unset rather than wrong.
        return None


def _to_result(item: dict[str, t.Any]) -> ResearchResult:
    return ResearchResult(
        source="web",
        title=item.get("title") or "(untitled)",
        url=item.get("url") or "",
        content=item.get("snippet") or "",
        published_at=_parse_published_at(item.get("published_at")),
        metadata={"domain": item.get("domain") or ""},
    )


@registry.register(
    ToolDefinition(
        name="search_web",
        description="Search the general web via the configured WebSearchProvider (read-only)",
        requires_approval=False,
        timeout_seconds=15,
    ),
    schema=SearchWebArgs,
)
async def execute(args: SearchWebArgs) -> dict[str, t.Any]:
    provider = get_default_provider()
    raw_results = await provider.search(args.query, args.limit)
    return {"results": [_to_result(item).model_dump(mode="json") for item in raw_results]}
