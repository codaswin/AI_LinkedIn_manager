"""Reddit research source — official OAuth2 (client-credentials) + REST search.

Credentials come from REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_USER_AGENT
env vars (never hardcoded). Missing credentials raise RedditConfigError, which
tools.sandbox.run_tool_sandboxed catches and turns into a normal
{"status": "error", ...} tool result — a missing Reddit config degrades this
one source gracefully rather than crashing the research run.
"""

from __future__ import annotations

import asyncio
import os
import typing as t
from datetime import datetime, timezone

import httpx
import structlog
from app.agents.research_schema import ResearchResult
from app.tools.http_utils import TTLCache, request_with_retry
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"

# Reddit issues client-credentials tokens with a 3600s lifetime; cached for
# slightly less so a call never straddles expiry mid-request.
_TOKEN_TTL_SECONDS = 3300
_MAX_SUBREDDITS_PER_CALL = 5

_token_cache = TTLCache(ttl_seconds=_TOKEN_TTL_SECONDS)


class RedditConfigError(RuntimeError):
    """Raised when REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET/REDDIT_USER_AGENT are not set."""


class SearchRedditArgs(BaseModel):
    query: str = Field(..., min_length=1)
    subreddits: list[str] | None = Field(
        default=None, description="Restrict search to these subreddits; omit to search all of Reddit"
    )
    limit: int = Field(default=10, ge=1, le=50)
    sort: t.Literal["relevance", "hot", "new", "top"] = "relevance"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RedditConfigError(f"{name} is not set. Set it in the environment before using search_reddit.")
    return value


async def _fetch_access_token(client: httpx.AsyncClient) -> str:
    client_id = _require_env("REDDIT_CLIENT_ID")
    client_secret = _require_env("REDDIT_CLIENT_SECRET")
    user_agent = _require_env("REDDIT_USER_AGENT")

    response = await request_with_retry(
        client,
        "POST",
        TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": user_agent},
    )
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise RedditConfigError(f"Reddit token response missing access_token: {payload!r}")
    return t.cast(str, token)


async def _get_access_token(client: httpx.AsyncClient) -> str:
    return await _token_cache.get_or_set("reddit:token", lambda: _fetch_access_token(client))


async def _search_one(
    client: httpx.AsyncClient, token: str, user_agent: str, query: str, subreddit: str | None, sort: str, limit: int
) -> list[dict[str, t.Any]]:
    headers = {"Authorization": f"Bearer {token}", "User-Agent": user_agent}
    if subreddit:
        url = f"{API_BASE}/r/{subreddit}/search"
        params = {"q": query, "restrict_sr": "1", "sort": sort, "limit": limit}
    else:
        url = f"{API_BASE}/search"
        params = {"q": query, "sort": sort, "limit": limit}

    response = await request_with_retry(client, "GET", url, headers=headers, params=params)
    payload = response.json()
    children = ((payload or {}).get("data") or {}).get("children") or []
    return [c["data"] for c in children if isinstance(c, dict) and isinstance(c.get("data"), dict)]


def _to_result(post: dict[str, t.Any]) -> ResearchResult:
    published_at = None
    if isinstance(post.get("created_utc"), (int, float)):
        published_at = datetime.fromtimestamp(post["created_utc"], tz=timezone.utc)

    permalink = post.get("permalink", "")
    return ResearchResult(
        source="reddit",
        title=post.get("title") or "(untitled)",
        url=f"https://reddit.com{permalink}" if permalink else post.get("url", ""),
        content=post.get("selftext") or "",
        author=post.get("author"),
        published_at=published_at,
        engagement={"score": post.get("score", 0) or 0, "comments": post.get("num_comments", 0) or 0},
        metadata={
            "reddit_id": post.get("id"),
            "subreddit": post.get("subreddit"),
            "permalink": f"https://reddit.com{permalink}" if permalink else None,
        },
    )


@registry.register(
    ToolDefinition(
        name="search_reddit",
        description="Search Reddit posts (optionally scoped to specific subreddits) via the official OAuth API",
        requires_approval=False,
        timeout_seconds=15,
    ),
    schema=SearchRedditArgs,
)
async def execute(args: SearchRedditArgs) -> dict[str, t.Any]:
    user_agent = _require_env("REDDIT_USER_AGENT")

    async with httpx.AsyncClient(timeout=8.0) as client:
        token = await _get_access_token(client)

        subreddits = (args.subreddits or [])[:_MAX_SUBREDDITS_PER_CALL]
        if subreddits:
            batches = await asyncio.gather(
                *(
                    _search_one(client, token, user_agent, args.query, sub, args.sort, args.limit)
                    for sub in subreddits
                ),
                return_exceptions=True,
            )
            posts: list[dict[str, t.Any]] = []
            for sub, batch in zip(subreddits, batches):
                if isinstance(batch, BaseException):
                    logger.warning("reddit_subreddit_search_failed", subreddit=sub, error=str(batch))
                    continue
                posts.extend(batch)
        else:
            posts = await _search_one(client, token, user_agent, args.query, None, args.sort, args.limit)

    seen_ids: set[str] = set()
    deduped: list[dict[str, t.Any]] = []
    for post in posts:
        post_id = post.get("id")
        if post_id is None or post_id in seen_ids:
            continue
        seen_ids.add(post_id)
        deduped.append(post)

    deduped.sort(key=lambda p: p.get("score", 0) or 0, reverse=True)
    top = deduped[: args.limit]

    return {"results": [_to_result(post).model_dump(mode="json") for post in top]}
