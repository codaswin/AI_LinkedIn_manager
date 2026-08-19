"""Cross-worker runtime activity board backed by Redis."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.shared_state import get_client
from app.tenancy.context import get_current_user_id

_TTL_SECONDS = 3600


def _hash_key(user_id: str) -> str:
    return f"runtime:activity:entries:{user_id}"


def _order_key(user_id: str) -> str:
    return f"runtime:activity:order:{user_id}"


def set_activity(agent: str, action: str, detail: str = "", source: str | None = None) -> str:
    user_id = get_current_user_id()
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
        pipe.hset(_hash_key(user_id), token, payload)
        pipe.zadd(_order_key(user_id), {token: started_at})
        pipe.expire(_hash_key(user_id), _TTL_SECONDS)
        pipe.expire(_order_key(user_id), _TTL_SECONDS)
        pipe.execute()
    return token


def clear_activity(token: str | int | None = None) -> None:
    user_id = get_current_user_id()
    client = get_client()
    if token is None:
        client.delete(_hash_key(user_id), _order_key(user_id))
        return
    value = str(token)
    with client.pipeline() as pipe:
        pipe.hdel(_hash_key(user_id), value)
        pipe.zrem(_order_key(user_id), value)
        pipe.execute()


def get_activity() -> dict[str, Any] | None:
    user_id = get_current_user_id()
    client = get_client()
    while True:
        tokens = client.zrevrange(_order_key(user_id), 0, 0)
        if not tokens:
            return None
        token = tokens[0]
        raw = client.hget(_hash_key(user_id), token)
        if raw is None:
            client.zrem(_order_key(user_id), token)
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
