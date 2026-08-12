from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from app.config import settings

_client: Redis | None = None


def get_client() -> Redis:
    global _client
    if _client is None:
        _client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def configure_client(client: Redis) -> None:
    """Override the module-level Redis client — used by tests to inject a fake/in-memory client."""
    global _client
    _client = client


async def _get(key: str) -> dict[str, Any] | None:
    raw = await get_client().get(key)
    return json.loads(raw) if raw else None


async def _set(key: str, data: dict[str, Any], ttl_seconds: int | None = None) -> None:
    await get_client().set(key, json.dumps(data), ex=ttl_seconds or settings.WORKING_MEMORY_TTL_SECONDS)


async def _clear(key: str) -> None:
    await get_client().delete(key)


async def get_current_draft(task_id: str) -> dict[str, Any] | None:
    return await _get(f"working:draft:{task_id}")


async def set_current_draft(task_id: str, draft: dict[str, Any], ttl_seconds: int | None = None) -> None:
    await _set(f"working:draft:{task_id}", draft, ttl_seconds)


async def clear_current_draft(task_id: str) -> None:
    await _clear(f"working:draft:{task_id}")


async def get_current_thread(thread_id: str) -> dict[str, Any] | None:
    return await _get(f"working:thread:{thread_id}")


async def set_current_thread(thread_id: str, thread_state: dict[str, Any], ttl_seconds: int | None = None) -> None:
    await _set(f"working:thread:{thread_id}", thread_state, ttl_seconds)


async def clear_current_thread(thread_id: str) -> None:
    await _clear(f"working:thread:{thread_id}")


async def get_approval_session(session_id: str) -> dict[str, Any] | None:
    return await _get(f"working:approval_session:{session_id}")


async def set_approval_session(session_id: str, session_state: dict[str, Any], ttl_seconds: int | None = None) -> None:
    await _set(f"working:approval_session:{session_id}", session_state, ttl_seconds)


async def clear_approval_session(session_id: str) -> None:
    await _clear(f"working:approval_session:{session_id}")
