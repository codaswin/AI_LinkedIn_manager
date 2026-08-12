"""System-wide kill switch — the one flag that halts all in-flight approvals.

Storage choice: an in-process flag guarded by threading.Lock, matching the
convention already established by llmops/cost_tracker.py and
tools/rate_limit.py (both in-memory, process-local, documented as a seam for
a later durable/shared backend) rather than skills/SAFETY.md's illustrative
Redis example. The contract this module must satisfy specifies synchronous,
no-`db`-argument signatures (`is_system_paused() -> bool`, etc.) — a
Redis-backed implementation would need to be awaited, which would break that
signature. Known limitation: pause state is per-process and does not survive
a restart or apply across multiple worker processes; swapping in a shared
backend later would not change any caller of this module.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_lock = threading.Lock()
_paused: bool = False
_pause_reason: str | None = None
_paused_by: str | None = None
_paused_at: datetime | None = None


def is_system_paused() -> bool:
    with _lock:
        return _paused


def pause_system(reason: str, paused_by: str) -> None:
    global _paused, _pause_reason, _paused_by, _paused_at
    with _lock:
        _paused = True
        _pause_reason = reason
        _paused_by = paused_by
        _paused_at = datetime.now(timezone.utc)
    logger.warning("system_paused", reason=reason, paused_by=paused_by)


def resume_system(resumed_by: str) -> None:
    global _paused, _pause_reason, _paused_by, _paused_at
    with _lock:
        _paused = False
        _pause_reason = None
        _paused_by = None
        _paused_at = None
    logger.warning("system_resumed", resumed_by=resumed_by)


def get_pause_info() -> dict[str, Any]:
    with _lock:
        return {
            "paused": _paused,
            "reason": _pause_reason,
            "paused_by": _paused_by,
            "paused_at": _paused_at,
        }


def reset_for_testing() -> None:
    """Test-only — production code must never call this."""
    resume_system(resumed_by="test-reset")
