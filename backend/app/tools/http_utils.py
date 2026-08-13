"""Shared async HTTP helpers for read-only research-source tools.

Every research-source tool (search_hackernews, search_reddit, search_web,
search_github, search_producthunt, search_rss) talks to an external HTTP
API. Rather than each tool hand-rolling its own retry/backoff/timeout/cache
logic, they all share this module — one place to get reliability behavior
right (product spec "Reliability" requirements: timeouts, retries with backoff,
graceful failures, caching) instead of six slightly-different copies.

Deliberately dependency-free (no tenacity/cachetools): the retry and cache
needs here are small and specific enough that hand-rolling them keeps the
new dependency surface to just `feedparser` (RSS parsing has no reasonable
substitute in the stdlib).
"""

from __future__ import annotations

import asyncio
import time
import typing as t

import httpx
import structlog

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF_BASE_SECONDS = 0.5

# Server errors and rate-limit responses are worth retrying; anything else
# (4xx auth/validation errors) is not — retrying a bad request just wastes
# the source's rate-limit budget for no chance of a different outcome.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class HTTPSourceError(RuntimeError):
    """Raised when a research-source HTTP call fails after all retries."""


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    retries: int = DEFAULT_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE_SECONDS,
    **kwargs: t.Any,
) -> httpx.Response:
    """One request, retried with exponential backoff on timeouts/5xx/429.

    Raises HTTPSourceError (never the raw httpx exception) once retries are
    exhausted, so every tool's except-clause only has one exception type to
    handle regardless of which source it's calling.
    """
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
        else:
            if response.status_code not in _RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                return response
            last_error = HTTPSourceError(
                f"{method} {url} returned retryable status {response.status_code}"
            )

        if attempt < retries:
            delay = backoff_base * (2**attempt)
            logger.warning(
                "http_request_retrying",
                url=url,
                attempt=attempt + 1,
                max_attempts=retries + 1,
                delay_seconds=delay,
                error=str(last_error),
            )
            await asyncio.sleep(delay)

    raise HTTPSourceError(f"{method} {url} failed after {retries + 1} attempt(s): {last_error}") from last_error


class TTLCache:
    """Tiny in-process TTL cache: avoids re-fetching the same upstream data

    (e.g. HN's top-story id list, a fresh Reddit OAuth token) more than once
    per `ttl_seconds` within a running process. Not shared across processes
    or persisted — that's fine, its only job is cutting duplicate calls
    within a single research run / short time window.
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, t.Any]] = {}
        self._lock = asyncio.Lock()

    async def get_or_set(self, key: str, factory: t.Callable[[], t.Awaitable[t.Any]]) -> t.Any:
        async with self._lock:
            cached = self._store.get(key)
            if cached is not None and cached[0] > time.monotonic():
                return cached[1]
        value = await factory()
        async with self._lock:
            self._store[key] = (time.monotonic() + self._ttl, value)
        return value

    def clear(self) -> None:
        self._store.clear()
