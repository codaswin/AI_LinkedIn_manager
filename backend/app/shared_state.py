"""Shared Redis client for cross-worker safety and runtime coordination state."""

from __future__ import annotations

from redis import Redis

from app.config import settings

_client: Redis | None = None


def get_client() -> Redis:
    global _client
    if _client is None:
        _client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def configure_client(client: Redis) -> None:
    global _client
    _client = client


def reset_client() -> None:
    global _client
    _client = None
