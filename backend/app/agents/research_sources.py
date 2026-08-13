"""One adapter per research source: uniform `(query, limit) -> list[ResearchResult]`

interface over each source's tool (tools.registry.execute_tool), so
research_pipeline.py never touches a source-specific raw shape or tool name
directly. Every adapter isolates its own failures — none of them ever raise;
a failing/misconfigured source degrades to an empty list plus a warning log,
which is what lets one dead source (e.g. no PRODUCTHUNT_TOKEN set) never
sink the rest of a research run.

X (Twitter) intentionally has no entry in SOURCE_ADAPTERS / DEFAULT_SOURCES —
only in OPTIONAL_SOURCES — so it is never chosen by source selection and is
only ever queried when a caller explicitly asks for "x" in `sources=`.
"""

from __future__ import annotations

import re
import typing as t

import structlog
from app.agents.research_schema import ResearchResult
from app.tools.registry import execute_tool
from pydantic import ValidationError

logger = structlog.get_logger(__name__)

SourceAdapter = t.Callable[[str, int], t.Awaitable[list[ResearchResult]]]

# Example-only, deliberately small: query keywords map to a *starting point*
# subreddit list, not an exhaustive taxonomy (product spec: "Do not hardcode research
# only to these communities. They are examples."). Unmatched queries fall
# back to a sitewide Reddit search (subreddits=None) rather than an empty list.
_SUBREDDIT_HINTS: dict[str, list[str]] = {
    "ai": ["LocalLLaMA", "MachineLearning", "artificial"],
    "agentic": ["LocalLLaMA", "MachineLearning"],
    "llm": ["LocalLLaMA", "MachineLearning"],
    "startup": ["startups", "SaaS", "Entrepreneur"],
    "startups": ["startups", "SaaS", "Entrepreneur"],
    "saas": ["SaaS", "startups"],
    "programming": ["programming", "webdev"],
    "developer": ["programming", "webdev"],
    "code": ["programming", "webdev"],
}


_WORD_RE = re.compile(r"[a-z0-9]+")


def _infer_subreddits(query: str) -> list[str] | None:
    # Whole-word matching, not substring: a naive substring check would
    # match "ai" inside "fundraising" or "advice" and misroute an unrelated
    # query into AI subreddits.
    query_words = set(_WORD_RE.findall(query.lower()))
    matched: list[str] = []
    for keyword, subs in _SUBREDDIT_HINTS.items():
        if keyword in query_words:
            for sub in subs:
                if sub not in matched:
                    matched.append(sub)
    return matched or None


def _extract_results(tool_result: dict[str, t.Any], source: str) -> list[ResearchResult]:
    if tool_result.get("status") != "success":
        if tool_result.get("status") == "error":
            logger.warning("research_source_tool_error", source=source, error=tool_result.get("error"))
        return []

    payload = tool_result.get("result") or {}
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return []

    results: list[ResearchResult] = []
    for item in raw_results:
        try:
            results.append(ResearchResult.model_validate(item))
        except ValidationError as exc:
            logger.warning("research_result_malformed", source=source, error=str(exc))
    return results


async def fetch_hackernews(query: str, limit: int) -> list[ResearchResult]:
    result = await execute_tool("search_hackernews", {"query": query, "limit": limit}, approved=False)
    return _extract_results(result, "hackernews")


async def fetch_reddit(query: str, limit: int, subreddits: list[str] | None = None) -> list[ResearchResult]:
    result = await execute_tool(
        "search_reddit",
        {"query": query, "limit": limit, "subreddits": subreddits or _infer_subreddits(query)},
        approved=False,
    )
    return _extract_results(result, "reddit")


async def fetch_web(query: str, limit: int) -> list[ResearchResult]:
    result = await execute_tool("search_web", {"query": query, "limit": limit}, approved=False)
    return _extract_results(result, "web")


async def fetch_github(query: str, limit: int) -> list[ResearchResult]:
    result = await execute_tool("search_github", {"query": query, "limit": limit}, approved=False)
    return _extract_results(result, "github")


async def fetch_producthunt(query: str, limit: int) -> list[ResearchResult]:
    result = await execute_tool("search_producthunt", {"query": query, "limit": limit}, approved=False)
    return _extract_results(result, "producthunt")


async def fetch_rss(query: str, limit: int, feeds: list[str] | None = None) -> list[ResearchResult]:
    result = await execute_tool(
        "search_rss", {"query": query, "limit": limit, "feed_urls": feeds}, approved=False
    )
    return _extract_results(result, "rss")


def _normalize_x_post(post: dict[str, t.Any]) -> ResearchResult | None:
    """Best-effort mapping of a raw Composio X-search post into ResearchResult.

    Composio's X toolkit response shape isn't pinned down anywhere else in
    this codebase (search_x_posts.py just passes it through as `{"posts":
    response}`), so this reads defensively via .get() with several plausible
    key names rather than assuming one exact shape.
    """
    text = post.get("text") or post.get("full_text") or ""
    post_id = post.get("id") or post.get("id_str")
    url = post.get("url") or (f"https://x.com/i/web/status/{post_id}" if post_id else None)
    if not url:
        return None
    author = post.get("author") or post.get("username") or post.get("user")
    if isinstance(author, dict):
        author = author.get("username") or author.get("name")

    published_at = None
    raw_created = post.get("created_at")
    if isinstance(raw_created, str):
        try:
            from datetime import datetime

            published_at = datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
        except ValueError:
            published_at = None

    return ResearchResult(
        source="x",
        title=text[:120] or "(untitled)",
        url=url,
        content=text,
        author=author,
        published_at=published_at,
        engagement={
            "likes": post.get("like_count", 0) or post.get("favorite_count", 0) or 0,
            "reposts": post.get("retweet_count", 0) or 0,
        },
        metadata={"x_id": post_id},
    )


async def fetch_x(query: str, limit: int) -> list[ResearchResult]:
    """Optional, explicit-opt-in-only source — see module docstring.

    Read-only by construction (search_x_posts.py itself asserts this at
    call time); this adapter changes none of that, it only normalizes the
    output.
    """
    result = await execute_tool("search_x_posts", {"query": query, "max_results": limit}, approved=False)
    if result.get("status") != "success":
        if result.get("status") == "error":
            logger.warning("research_source_tool_error", source="x", error=result.get("error"))
        return []
    posts = (result.get("result") or {}).get("posts") or []
    if not isinstance(posts, list):
        return []
    out: list[ResearchResult] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        normalized = _normalize_x_post(post)
        if normalized is not None:
            out.append(normalized)
    return out[:limit]


# The default, source-selection-eligible sources. X is deliberately absent —
# see module docstring.
SOURCE_ADAPTERS: dict[str, SourceAdapter] = {
    "hackernews": fetch_hackernews,
    "reddit": fetch_reddit,
    "web": fetch_web,
    "github": fetch_github,
    "producthunt": fetch_producthunt,
    "rss": fetch_rss,
}

OPTIONAL_SOURCES: dict[str, SourceAdapter] = {
    "x": fetch_x,
}

ALL_SOURCES: dict[str, SourceAdapter] = {**SOURCE_ADAPTERS, **OPTIONAL_SOURCES}
DEFAULT_SOURCES: tuple[str, ...] = tuple(SOURCE_ADAPTERS)


# Thin, explicitly-named aliases over the fetch_* adapters above, for callers
# that want one source directly rather than going through research_pipeline's
# selection/dedupe/rank. Pure delegation — no separate implementation to
# maintain in parallel.
async def research_hackernews(query: str, limit: int = 20) -> list[ResearchResult]:
    return await fetch_hackernews(query, limit)


async def research_github(query: str, limit: int = 20) -> list[ResearchResult]:
    return await fetch_github(query, limit)


async def research_producthunt(query: str, limit: int = 20) -> list[ResearchResult]:
    return await fetch_producthunt(query, limit)


async def research_rss(query: str, feeds: list[str] | None = None, limit: int = 20) -> list[ResearchResult]:
    return await fetch_rss(query, limit, feeds=feeds)


async def research_reddit(query: str, limit: int = 20) -> list[ResearchResult]:
    return await fetch_reddit(query, limit)
