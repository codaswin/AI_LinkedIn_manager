"""In-memory "what is the system doing right now" board.

Powers the dashboard's live activity animation (frontend/src/components/
ActivityBanner.tsx) — the frontend polls GET /activity every ~1.2s and
renders a different animated graphic depending on which agent/source is
currently active (e.g. a searching-pulse over the Reddit icon while
research_pipeline is querying Reddit, a writing animation while the
Content Writer drafts).

A STACK, not a single slot: research_pipeline.py fetches multiple sources
CONCURRENTLY (asyncio.gather), so multiple activity() context managers are
open at once. An earlier single-slot design broke under exactly this case —
whichever concurrent task finished first cleared the slot unconditionally,
hiding every other source still genuinely in flight, sometimes for the
entire duration of a request. Each set_activity() call now gets its own
token and only removes its own stack entry on exit; get_activity() reports
the most recently started entry still active, and the board only goes idle
once every concurrent task has finished — not just the fastest one.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

_lock = threading.Lock()
_id_counter = itertools.count()


@dataclass
class _Activity:
    token: int
    agent: str
    action: str
    detail: str = ""
    source: str | None = None
    started_at: float = field(default_factory=time.monotonic)


_stack: list[_Activity] = []


def set_activity(agent: str, action: str, detail: str = "", source: str | None = None) -> int:
    """Pushes a new active entry, returning a token. Pass that token to

    clear_activity() to remove exactly this entry — never call
    clear_activity() to wipe the whole board from concurrent code, or
    you'll reintroduce the "fastest task clears everyone" bug this module's
    docstring describes.
    """
    entry = _Activity(token=next(_id_counter), agent=agent, action=action, detail=detail, source=source)
    with _lock:
        _stack.append(entry)
    return entry.token


def clear_activity(token: int | None = None) -> None:
    """token=None clears the entire board (test/manual use only). Otherwise

    removes only the entry that token identifies, leaving any other
    concurrently-active entries untouched.
    """
    with _lock:
        if token is None:
            _stack.clear()
        else:
            _stack[:] = [e for e in _stack if e.token != token]


def get_activity() -> dict[str, Any] | None:
    """None means idle — nothing is currently running. Otherwise reports the

    most recently started still-active entry (if several sources are
    running concurrently, this is "last known active step," not a full
    list of everything in flight — see module docstring).
    """
    with _lock:
        if not _stack:
            return None
        current = _stack[-1]
        return {
            "agent": current.agent,
            "action": current.action,
            "detail": current.detail,
            "source": current.source,
            "elapsed_seconds": round(time.monotonic() - current.started_at, 1),
        }


@contextmanager
def activity(agent: str, action: str, detail: str = "", source: str | None = None) -> Iterator[None]:
    """Wrap one step of agent work: pushes onto the board on entry, removes

    only that entry on exit — including on exception, so a failed step
    never leaves a stale "in progress" entry behind forever.
    """
    token = set_activity(agent, action, detail, source)
    try:
        yield
    finally:
        clear_activity(token)


def reset_for_testing() -> None:
    with _lock:
        _stack.clear()
