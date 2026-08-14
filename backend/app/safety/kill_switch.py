"""System-wide kill switch stored in Redis for cross-worker consistency."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from app.shared_state import get_client

logger = structlog.get_logger(__name__)
_KEY = "safety:kill-switch"


def is_system_paused() -> bool:
    return get_client().hget(_KEY, "paused") == "1"


def pause_system(reason: str, paused_by: str) -> None:
    get_client().hset(
        _KEY,
        mapping={
            "paused": "1",
            "reason": reason,
            "paused_by": paused_by,
            "paused_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.warning("system_paused", reason=reason, paused_by=paused_by)


def resume_system(resumed_by: str) -> None:
    get_client().delete(_KEY)
    logger.warning("system_resumed", resumed_by=resumed_by)


def get_pause_info() -> dict[str, Any]:
    state = get_client().hgetall(_KEY)
    paused_at = state.get("paused_at")
    return {
        "paused": state.get("paused") == "1",
        "reason": state.get("reason"),
        "paused_by": state.get("paused_by"),
        "paused_at": datetime.fromisoformat(paused_at) if paused_at else None,
    }


def reset_for_testing() -> None:
    get_client().delete(_KEY)
