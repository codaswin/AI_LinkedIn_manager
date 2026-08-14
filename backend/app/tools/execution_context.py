"""Per-execution metadata propagated from an approval into tool implementations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_idempotency_key: ContextVar[str | None] = ContextVar("tool_idempotency_key", default=None)


def current_idempotency_key() -> str | None:
    return _idempotency_key.get()


@contextmanager
def idempotency_scope(value: str) -> Iterator[None]:
    token = _idempotency_key.set(value)
    try:
        yield
    finally:
        _idempotency_key.reset(token)
