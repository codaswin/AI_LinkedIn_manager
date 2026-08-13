"""Hacker News research source — official Firebase API, no API key required.

https://hacker-news.firebaseio.com/v0/ : {top,new,best}stories.json give ID
lists; item/{id}.json gives the story itself. Deliberately does not fetch
"hundreds of item details" per product spec guidance: it only ever expands a bounded
candidate window (_CANDIDATE_MULTIPLIER * limit, capped at _MAX_CANDIDATES)
of the id list before filtering, and fetches item details with bounded
concurrency (_ITEM_FETCH_CONCURRENCY).
"""

from __future__ import annotations

import asyncio
import typing as t
from datetime import datetime, timezone

import httpx
import structlog
from app.agents.research_schema import ResearchResult
from app.tools.http_utils import TTLCache, request_with_retry
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

BASE_URL = "https://hacker-news.firebaseio.com/v0"

_CANDIDATE_MULTIPLIER = 8
_MAX_CANDIDATES = 120
_ITEM_FETCH_CONCURRENCY = 8
_MAX_COMMENTS_PER_STORY = 3

_id_list_cache = TTLCache(ttl_seconds=300)
_item_cache = TTLCache(ttl_seconds=300)


class SearchHackerNewsArgs(BaseModel):
    query: str = Field(..., min_length=1, description="Keywords to filter stories by")
    story_type: t.Literal["top", "new", "best", "show", "ask", "job"] = "top"
    limit: int = Field(default=10, ge=1, le=30)
    include_comments: bool = False


def _keywords(text: str) -> set[str]:
    return {tok.lower() for tok in text.split() if tok}


async def _fetch_json(client: httpx.AsyncClient, url: str) -> t.Any:
    response = await request_with_retry(client, "GET", url)
    return response.json()


async def _get_story_ids(client: httpx.AsyncClient, story_type: str) -> list[int]:
    async def factory() -> list[int]:
        data = await _fetch_json(client, f"{BASE_URL}/{story_type}stories.json")
        return list(data) if isinstance(data, list) else []

    return await _id_list_cache.get_or_set(f"hn:{story_type}", factory)


async def _get_item(client: httpx.AsyncClient, item_id: int) -> dict[str, t.Any] | None:
    async def factory() -> dict[str, t.Any] | None:
        data = await _fetch_json(client, f"{BASE_URL}/item/{item_id}.json")
        return data if isinstance(data, dict) else None

    return await _item_cache.get_or_set(f"hn:item:{item_id}", factory)


async def _fetch_items(client: httpx.AsyncClient, ids: list[int]) -> list[dict[str, t.Any]]:
    semaphore = asyncio.Semaphore(_ITEM_FETCH_CONCURRENCY)

    async def _bounded(item_id: int) -> dict[str, t.Any] | None:
        async with semaphore:
            try:
                return await _get_item(client, item_id)
            except Exception as exc:  # noqa: BLE001 — one bad item must not sink the whole batch
                logger.warning("hn_item_fetch_failed", item_id=item_id, error=str(exc))
                return None

    items = await asyncio.gather(*(_bounded(i) for i in ids))
    return [item for item in items if item is not None]


def _matches_query(item: dict[str, t.Any], query_keywords: set[str]) -> bool:
    haystack = f"{item.get('title', '')} {item.get('text', '')}".lower()
    return any(kw in haystack for kw in query_keywords)


async def _fetch_top_comments(client: httpx.AsyncClient, item: dict[str, t.Any]) -> list[str]:
    kid_ids = (item.get("kids") or [])[:_MAX_COMMENTS_PER_STORY]
    if not kid_ids:
        return []
    comments = await _fetch_items(client, kid_ids)
    return [c["text"] for c in comments if isinstance(c.get("text"), str) and c.get("text")]


def _to_result(item: dict[str, t.Any], story_type: str, top_comments: list[str]) -> ResearchResult:
    item_id = item.get("id")
    published_at = None
    if isinstance(item.get("time"), (int, float)):
        published_at = datetime.fromtimestamp(item["time"], tz=timezone.utc)

    return ResearchResult(
        source="hackernews",
        title=item.get("title") or "(untitled)",
        url=item.get("url") or f"https://news.ycombinator.com/item?id={item_id}",
        content=item.get("text") or "",
        author=item.get("by"),
        published_at=published_at,
        engagement={"score": item.get("score", 0) or 0, "comments": item.get("descendants", 0) or 0},
        metadata={
            "hn_id": item_id,
            "story_type": story_type,
            "discussion_url": f"https://news.ycombinator.com/item?id={item_id}",
            "top_comments": top_comments,
        },
    )


@registry.register(
    ToolDefinition(
        name="search_hackernews",
        description="Search Hacker News (top/new/best stories) for a query via the official Firebase API (read-only, no API key)",
        requires_approval=False,
        timeout_seconds=15,
    ),
    schema=SearchHackerNewsArgs,
)
async def execute(args: SearchHackerNewsArgs) -> dict[str, t.Any]:
    query_keywords = _keywords(args.query)
    candidate_count = min(args.limit * _CANDIDATE_MULTIPLIER, _MAX_CANDIDATES)

    async with httpx.AsyncClient(timeout=8.0) as client:
        story_ids = await _get_story_ids(client, args.story_type)
        candidate_ids = story_ids[:candidate_count]
        items = await _fetch_items(client, candidate_ids)

        matched = [item for item in items if _matches_query(item, query_keywords)]
        matched.sort(key=lambda i: i.get("score", 0) or 0, reverse=True)
        matched = matched[: args.limit]

        results: list[ResearchResult] = []
        for item in matched:
            top_comments: list[str] = []
            if args.include_comments:
                try:
                    top_comments = await _fetch_top_comments(client, item)
                except Exception as exc:  # noqa: BLE001 — comments are a bonus, never fatal
                    logger.warning("hn_comments_fetch_failed", item_id=item.get("id"), error=str(exc))
            results.append(_to_result(item, args.story_type, top_comments))

    return {"results": [r.model_dump(mode="json") for r in results]}
