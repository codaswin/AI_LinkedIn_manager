from __future__ import annotations

import os
import threading
from collections import defaultdict
from datetime import date, datetime, timezone


class RateLimitConfigError(RuntimeError):
    pass


class RateLimitExceededError(RuntimeError):
    pass


class DailyRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, dict[date, int]] = defaultdict(dict)

    def check_and_increment(self, action: str, env_var: str) -> int:
        raw_cap = os.environ.get(env_var)
        if raw_cap is None:
            raise RateLimitConfigError(
                f"{env_var} is not set; refusing to run '{action}' without a configured daily rate cap."
            )
        try:
            cap = int(raw_cap)
        except ValueError as exc:
            raise RateLimitConfigError(f"{env_var} must be an integer, got {raw_cap!r}") from exc

        today = datetime.now(tz=timezone.utc).date()
        with self._lock:
            day_counts = self._counts[action]
            used = day_counts.get(today, 0)
            if used >= cap:
                raise RateLimitExceededError(
                    f"Daily rate cap for '{action}' reached ({used}/{cap}, set via {env_var})."
                )
            day_counts[today] = used + 1
            return day_counts[today]

    def peek(self, action: str, env_var: str) -> tuple[int, int]:
        """Read-only sibling to check_and_increment: returns (used_today, cap)
        without incrementing. Exists for pre-flight advisory checks (e.g.
        safety.cost_cap.check_all_caps) that must not themselves consume a
        rate-limit slot — only a tool's own check_and_increment call inside
        execute() is allowed to do that; otherwise every pre-flight check
        would silently halve the effective daily allowance.
        """
        raw_cap = os.environ.get(env_var)
        if raw_cap is None:
            raise RateLimitConfigError(
                f"{env_var} is not set; refusing to check '{action}' without a configured daily rate cap."
            )
        try:
            cap = int(raw_cap)
        except ValueError as exc:
            raise RateLimitConfigError(f"{env_var} must be an integer, got {raw_cap!r}") from exc

        today = datetime.now(tz=timezone.utc).date()
        with self._lock:
            used = self._counts[action].get(today, 0)
        return used, cap

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


daily_rate_limiter = DailyRateLimiter()
