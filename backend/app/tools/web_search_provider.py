"""Pluggable web-search provider interface.

No web-search tool/provider existed anywhere in this codebase before this
refactor (confirmed by inspection), so search_web.py is written against
this interface rather than any one vendor's API — swapping providers later
(Brave, SerpAPI, Bing, Google CSE, ...) only means adding a new
WebSearchProvider subclass and pointing WEB_SEARCH_PROVIDER at it; nothing
in search_web.py or the research pipeline changes.

DuckDuckGo (via the `ddgs` package) is the default provider: no API key
needed, unlike Brave. Brave remains available (WEB_SEARCH_PROVIDER=brave)
for anyone who already has a key and wants its result quality/quota.
"""

from __future__ import annotations

import asyncio
import os
import typing as t
from abc import ABC, abstractmethod
from urllib.parse import urlsplit

import httpx
import structlog
from app.tenancy.credentials import resolve_credential
from app.tools.http_utils import request_with_retry
from ddgs import DDGS

logger = structlog.get_logger(__name__)


class WebSearchResultDict(t.TypedDict, total=False):
    title: str
    url: str
    snippet: str
    domain: str
    published_at: str | None


class WebSearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, limit: int) -> list[WebSearchResultDict]: ...


class NullWebSearchProvider(WebSearchProvider):
    """Used when no provider is configured — degrades gracefully to zero results

    (same pattern as search_knowledge_base.py's `_retrieve = None` fallback)
    rather than raising, since web search is one optional source of several.
    """

    async def search(self, query: str, limit: int) -> list[WebSearchResultDict]:
        return []


class BraveWebSearchProvider(WebSearchProvider):
    """Brave Search API — a plain REST endpoint (no SDK needed), chosen as the

    default concrete provider because it needs nothing beyond one API key
    header, which keeps this file dependency-free like the rest of the
    research tools.
    """

    BASE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, limit: int) -> list[WebSearchResultDict]:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await request_with_retry(
                client,
                "GET",
                self.BASE_URL,
                headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
                params={"q": query, "count": limit},
            )
        payload = response.json()
        results = ((payload or {}).get("web") or {}).get("results") or []
        out: list[WebSearchResultDict] = []
        for item in results[:limit]:
            out.append(
                {
                    "title": item.get("title") or "(untitled)",
                    "url": item.get("url") or "",
                    "snippet": item.get("description") or "",
                    "domain": item.get("profile", {}).get("long_name") or item.get("meta_url", {}).get("hostname", ""),
                    "published_at": item.get("age") or item.get("page_age"),
                }
            )
        return out


class DuckDuckGoWebSearchProvider(WebSearchProvider):
    """DuckDuckGo via the `ddgs` package — no API key required.

    DDGS().text() is a synchronous call under the hood (no async client of
    its own), so it's run via asyncio.to_thread rather than blocking the
    event loop — same pattern as rag/ingest.py's _upsert wrapping its sync
    FAISS calls.
    """

    def _search_sync(self, query: str, limit: int) -> list[dict[str, t.Any]]:
        with DDGS() as ddgs:
            return ddgs.text(query, max_results=limit)

    async def search(self, query: str, limit: int) -> list[WebSearchResultDict]:
        raw_results = await asyncio.to_thread(self._search_sync, query, limit)
        out: list[WebSearchResultDict] = []
        for item in raw_results[:limit]:
            url = item.get("href") or ""
            out.append(
                {
                    "title": item.get("title") or "(untitled)",
                    "url": url,
                    "snippet": item.get("body") or "",
                    "domain": urlsplit(url).netloc,
                    "published_at": None,
                }
            )
        return out


def get_default_provider() -> WebSearchProvider:
    """Resolved fresh on every call (not module-cached) so tests that

    monkeypatch env vars see the effect immediately — matches
    content_strategist.build_run_config()'s pattern.
    """
    provider_name = os.environ.get("WEB_SEARCH_PROVIDER", "duckduckgo").lower()
    if provider_name == "none":
        return NullWebSearchProvider()
    if provider_name == "duckduckgo":
        return DuckDuckGoWebSearchProvider()
    if provider_name == "brave":
        api_key = resolve_credential("BRAVE_SEARCH_API_KEY")
        if not api_key:
            logger.warning("web_search_provider_unconfigured", provider=provider_name)
            return NullWebSearchProvider()
        return BraveWebSearchProvider(api_key)

    logger.warning("web_search_provider_unknown", provider=provider_name)
    return NullWebSearchProvider()
