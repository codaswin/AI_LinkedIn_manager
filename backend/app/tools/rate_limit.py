from __future__ import annotations

import os
from datetime import datetime, time, timedelta, timezone

from redis.exceptions import WatchError

from app.shared_state import get_client


class RateLimitConfigError(RuntimeError):
    pass


class RateLimitExceededError(RuntimeError):
    pass


def _cap(env_var: str) -> int:
    raw = os.environ.get(env_var)
    if raw is None:
        raise RateLimitConfigError(f"{env_var} is not set; refusing to run without a configured daily rate cap.")
    try:
        cap = int(raw)
    except ValueError as exc:
        raise RateLimitConfigError(f"{env_var} must be an integer, got {raw!r}") from exc
    if cap < 0:
        raise RateLimitConfigError(f"{env_var} must be >= 0, got {cap}")
    return cap


def _key(action: str) -> str:
    return f"safety:rate:{datetime.now(timezone.utc):%Y-%m-%d}:{action}"


def _ttl() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return max(60, int((tomorrow - now).total_seconds()) + 60)


class DailyRateLimiter:
    def check_and_increment(self, action: str, env_var: str) -> int:
        cap = _cap(env_var)
        client = get_client()
        key = _key(action)
        while True:
            try:
                with client.pipeline() as pipe:
                    pipe.watch(key)
                    used = int(pipe.get(key) or 0)
                    if used >= cap:
                        pipe.unwatch()
                        raise RateLimitExceededError(
                            f"Daily rate cap for '{action}' reached ({used}/{cap}, set via {env_var})."
                        )
                    pipe.multi()
                    pipe.set(key, used + 1, ex=_ttl())
                    pipe.execute()
                    return used + 1
            except WatchError:
                continue

    def peek(self, action: str, env_var: str) -> tuple[int, int]:
        cap = _cap(env_var)
        return int(get_client().get(_key(action)) or 0), cap

    def reset(self) -> None:
        client = get_client()
        keys = list(client.scan_iter(match="safety:rate:*"))
        if keys:
            client.delete(*keys)


daily_rate_limiter = DailyRateLimiter()
