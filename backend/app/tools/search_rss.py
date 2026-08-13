"""RSS/Atom research source — no API key required.

Feed URLs, in priority order: the `feed_urls` argument, then the RSS_FEEDS
env var (comma-separated), then DEFAULT_RSS_FEEDS below — so this source
works out of the box with no configuration, but a deployment can override
it in exactly one place (RSS_FEEDS) without touching code. Uses
`feedparser` (the standard mature RSS/Atom parsing library for Python;
added as a new dependency — see requirements.txt) rather than hand-rolling
XML parsing, since real-world feeds vary a lot in dialect (RSS 2.0, Atom,
RDF, ...).
"""

from __future__ import annotations

import asyncio
import os
import re
import typing as t
from datetime import datetime, timezone

import feedparser
import httpx
import structlog
from app.agents.research_schema import ResearchResult
from app.tools.http_utils import request_with_retry
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# Starter set so RSS works with zero configuration (product spec: "Use these feeds
# initially"). Override entirely via the RSS_FEEDS env var — never edit this
# list to add a feed for one deployment; that's what the env var is for.
DEFAULT_RSS_FEEDS: list[str] = [
    "https://techcrunch.com/feed/",
    "https://venturebeat.com/feed/",
    "https://hnrss.org/best",
    "https://hnrss.org/newest",
    "https://www.ycombinator.com/blog/rss",
    "https://www.technologyreview.com/feed/",
    "https://blog.google/technology/ai/rss/",
    "https://huggingface.co/blog/feed.xml",
]


class SearchRSSArgs(BaseModel):
    query: str = Field(..., min_length=1)
    feed_urls: list[str] | None = Field(default=None, description="Overrides RSS_FEEDS env var when provided")
    limit: int = Field(default=10, ge=1, le=30)


def _configured_feed_urls() -> list[str]:
    raw = os.environ.get("RSS_FEEDS", "")
    configured = [url.strip() for url in raw.split(",") if url.strip()]
    return configured or list(DEFAULT_RSS_FEEDS)


_WORD_RE = re.compile(r"[a-z0-9]+")


def _keywords(text: str) -> set[str]:
    # Whole-word tokens, not a plain .split(): a naive substring/whitespace
    # split lets short queries like "AI" false-match "again"/"main"/"said"
    # (all contain "ai"), which was observed to gut the filter almost
    # entirely on a real "AI agents" query against live feeds.
    return set(_WORD_RE.findall(text.lower()))


def _matches_query(result: ResearchResult, query_keywords: set[str]) -> bool:
    haystack_words = _keywords(f"{result.title} {result.content}")
    return bool(query_keywords & haystack_words)


def _entry_published_at(entry: t.Any) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        year, month, day, hour, minute, second = parsed[:6]
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _to_result(entry: t.Any, feed_url: str, feed_title: str) -> ResearchResult:
    return ResearchResult(
        source="rss",
        title=entry.get("title") or "(untitled)",
        url=entry.get("link") or "",
        content=entry.get("summary") or "",
        author=entry.get("author"),
        published_at=_entry_published_at(entry),
        metadata={"feed_url": feed_url, "feed_title": feed_title},
    )


async def _fetch_feed(client: httpx.AsyncClient, feed_url: str) -> list[ResearchResult]:
    response = await request_with_retry(client, "GET", feed_url)
    parsed = feedparser.parse(response.text)
    feed_title = (parsed.feed or {}).get("title", feed_url)
    return [_to_result(entry, feed_url, feed_title) for entry in parsed.entries]


@registry.register(
    ToolDefinition(
        name="search_rss",
        description="Fetch and filter entries from configured RSS/Atom feeds (read-only, no API key)",
        requires_approval=False,
        timeout_seconds=15,
    ),
    schema=SearchRSSArgs,
)
async def execute(args: SearchRSSArgs) -> dict[str, t.Any]:
    feed_urls = args.feed_urls or _configured_feed_urls()
    if not feed_urls:
        return {"results": [], "warning": "No RSS feeds configured (set RSS_FEEDS or pass feed_urls)"}

    # RSS feed URLs commonly 301/308-redirect (e.g. venturebeat.com/feed/,
    # blog.google's AI feed) — httpx doesn't follow redirects by default, so
    # without this every redirecting feed would silently fail every time.
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        batches = await asyncio.gather(*(_fetch_feed(client, url) for url in feed_urls), return_exceptions=True)

    all_entries: list[ResearchResult] = []
    for url, batch in zip(feed_urls, batches):
        if isinstance(batch, BaseException):
            logger.warning("rss_feed_fetch_failed", feed_url=url, error=str(batch))
            continue
        all_entries.extend(batch)

    query_keywords = _keywords(args.query)
    matched = [e for e in all_entries if _matches_query(e, query_keywords)]
    if not matched:
        # No keyword match anywhere: RSS entries are official/curated content
        # (company blogs, research announcements) worth surfacing even off
        # exact keywords, so fall back to most-recent-first rather than empty.
        matched = all_entries

    matched.sort(key=lambda e: e.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    top = matched[: args.limit]

    return {"results": [r.model_dump(mode="json") for r in top]}
