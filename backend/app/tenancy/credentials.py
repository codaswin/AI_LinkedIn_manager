"""Per-user credential overlay — the replacement for writing pasted API keys

into process-wide `os.environ`. Every dashboard user gets their own
isolated slice of env-var-shaped values (COMPOSIO_API_KEY, OPENAI_API_KEY,
REDDIT_CLIENT_ID, ...); a value saved by one user is never visible to
another. `app.memory.platform_credentials` is the only writer (it owns the
durable/encrypted copy in Postgres); every credential consumer
(`composio_client`, `openai_client`, `search_reddit`, ...) is a reader via
`resolve_credential`.

Deliberately has zero dependency on `app.memory`/`app.tools`/`app.llmops` —
those modules import *this* one, not the other way around, which is what
keeps `platform_credentials.py` (which itself imports the client modules
for `_CLIENT_CACHE_RESETTERS`) free of an import cycle.
"""

from __future__ import annotations

import threading

from app.tenancy.context import get_current_user_id

_lock = threading.Lock()
_overlay: dict[str, dict[str, str]] = {}


def set_credential(user_id: str, env_var: str, value: str) -> None:
    with _lock:
        _overlay.setdefault(user_id, {})[env_var] = value


def clear_credential(user_id: str, env_var: str) -> None:
    with _lock:
        _overlay.get(user_id, {}).pop(env_var, None)


def clear_user(user_id: str) -> None:
    with _lock:
        _overlay.pop(user_id, None)


def load_overlay(values_by_user: dict[str, dict[str, str]]) -> None:
    """Bulk (re)population at startup — replaces the whole overlay wholesale."""
    with _lock:
        _overlay.clear()
        for user_id, values in values_by_user.items():
            _overlay[user_id] = dict(values)


def resolve_credential(env_var: str) -> str | None:
    """Look up `env_var` for the current request/job's user. No shared

    fallback: a value only exists here if that specific user saved it.
    """
    user_id = get_current_user_id()
    with _lock:
        return _overlay.get(user_id, {}).get(env_var)
