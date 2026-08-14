"""Cross-worker runtime activity board backed by Redis."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.shared_state import get_client

_HASH_KEY = "runtime:activity:entries"
_ORDER_KEY = "runtime:activity:order"
_TTL_SECONDS = 3600


def set_activity(agent: str, action: str, detail: str = "", source: str | None = None) -> str:
    token = str(uuid.uuid4())
    started_at = time.time()
    payload = json.dumps(
        {
            "agent": agent,
            "action": action,
            "detail": detail,
            "source": source,
            "started_at": started_at,
        }
    )
    client = get_client()
    with client.pipeline() as pipe:
        pipe.hset(_HASH_KEY, token, payload)
        pipe.zadd(_ORDER_KEY, {token: started_at})
        pipe.expire(_HASH_KEY, _TTL_SECONDS)
        pipe.expire(_ORDER_KEY, _TTL_SECONDS)
        pipe.execute()
    return token


def clear_activity(token: str | int | None = None) -> None:
    client = get_client()
    if token is None:
        client.delete(_HASH_KEY, _ORDER_KEY)
        return
    value = str(token)
    with client.pipeline() as pipe:
        pipe.hdel(_HASH_KEY, value)
        pipe.zrem(_ORDER_KEY, value)
        pipe.execute()


def get_activity() -> dict[str, Any] | None:
    client = get_client()
    while True:
        tokens = client.zrevrange(_ORDER_KEY, 0, 0)
        if not tokens:
            return None
        token = tokens[0]
        raw = client.hget(_HASH_KEY, token)
        if raw is None:
            client.zrem(_ORDER_KEY, token)
            continue
        current = json.loads(raw)
        return {
            "agent": current["agent"],
            "action": current["action"],
            "detail": current["detail"],
            "source": current["source"],
            "elapsed_seconds": round(max(0.0, time.time() - float(current["started_at"])), 1),
        }


@contextmanager
def activity(agent: str, action: str, detail: str = "", source: str | None = None) -> Iterator[None]:
    token = set_activity(agent, action, detail, source)
    try:
        yield
    finally:
        clear_activity(token)


def reset_for_testing() -> None:
    clear_activity()
