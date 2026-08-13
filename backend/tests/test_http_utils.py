from __future__ import annotations

import httpx
import pytest
from app.tools.http_utils import HTTPSourceError, TTLCache, request_with_retry


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)  # type: ignore[arg-type]


async def test_succeeds_on_first_try(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_request(self, method, url, **kwargs):
        nonlocal calls
        calls += 1
        return _Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    async with httpx.AsyncClient() as client:
        response = await request_with_retry(client, "GET", "https://example.com")
    assert response.status_code == 200
    assert calls == 1


async def test_retries_on_retryable_status_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_request(self, method, url, **kwargs):
        nonlocal calls
        calls += 1
        return _Response(503) if calls == 1 else _Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    async with httpx.AsyncClient() as client:
        response = await request_with_retry(client, "GET", "https://example.com", backoff_base=0.001)
    assert response.status_code == 200
    assert calls == 2


async def test_raises_http_source_error_after_exhausting_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request(self, method, url, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    async with httpx.AsyncClient() as client:
        with pytest.raises(HTTPSourceError):
            await request_with_retry(client, "GET", "https://example.com", retries=1, backoff_base=0.001)


async def test_non_retryable_4xx_raises_immediately_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_request(self, method, url, **kwargs):
        nonlocal calls
        calls += 1
        return _Response(404)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await request_with_retry(client, "GET", "https://example.com")
    assert calls == 1


async def test_ttl_cache_returns_cached_value_within_ttl() -> None:
    cache = TTLCache(ttl_seconds=60)
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        return "value"

    first = await cache.get_or_set("key", factory)
    second = await cache.get_or_set("key", factory)
    assert first == second == "value"
    assert calls == 1


async def test_ttl_cache_expires_and_refetches() -> None:
    cache = TTLCache(ttl_seconds=-1)  # already expired
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        return calls

    first = await cache.get_or_set("key", factory)
    second = await cache.get_or_set("key", factory)
    assert first == 1
    assert second == 2


async def test_ttl_cache_clear_forces_refetch() -> None:
    cache = TTLCache(ttl_seconds=60)
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        return calls

    await cache.get_or_set("key", factory)
    cache.clear()
    second = await cache.get_or_set("key", factory)
    assert second == 2
