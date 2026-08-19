"""Which dashboard user the current request/job is acting as.

Every credential lookup, cached SDK client, and (in later stages) every
scoped query needs to know this without every function in the call chain
threading a `user_id` parameter through by hand — a `ContextVar` lets
`main.py`'s auth middleware (or the scheduler's per-user job loop) set it
once at the top of a request/iteration, and anything downstream just asks.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


def set_current_user_id(user_id: str) -> Token[str | None]:
    return _current_user_id.set(user_id)


def get_current_user_id() -> str:
    user_id = _current_user_id.get()
    if user_id is None:
        raise RuntimeError(
            "No current user is set on this request/job — set_current_user_id() must be called "
            "before any tenant-scoped code path runs."
        )
    return user_id


def reset_current_user_id(token: Token[str | None]) -> None:
    _current_user_id.reset(token)
