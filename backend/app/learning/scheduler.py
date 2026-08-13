"""Periodic trigger for the reflection job — the Post-MVP roadmap item

"self-learning loop running on an actual schedule." Uses APScheduler's
AsyncIOScheduler since the rest of this codebase is asyncio-native.

Deliberately NOT real-time (skills/LEARNING.md Best Practices: "the
reflection job runs on a schedule, never inline in the request path").
Default cadence is weekly, matching that same file's own precedent.
"""

from __future__ import annotations

import os

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = structlog.get_logger(__name__)

_DEFAULT_INTERVAL_HOURS = 24.0 * 7  # weekly

_scheduler: AsyncIOScheduler | None = None


def _interval_hours() -> float:
    raw = os.environ.get("REFLECTION_JOB_INTERVAL_HOURS")
    return float(raw) if raw else _DEFAULT_INTERVAL_HOURS


async def _run_reflection_job() -> None:
    """Wraps run_reflection with its own DB session and a real llm_client —

    a scheduled job can't reuse a request-scoped session, and needs
    model_router.route_and_call rather than a test fake. Both imports are
    deferred to call time so importing this module never requires either
    to already exist.
    """
    from app.database import get_session_factory
    from app.learning.feedback import DEFAULT_ENGAGEMENT_LAG_DAYS
    from app.learning.reflection_job import run_reflection
    from app.llmops.model_router import route_and_call

    factory = get_session_factory()
    async with factory() as db:
        try:
            result = await run_reflection(db, route_and_call, days=DEFAULT_ENGAGEMENT_LAG_DAYS)
            logger.info(
                "scheduled_reflection_job_completed",
                ran=result["ran"],
                feedback_count=result["feedback_count"],
                proposal_count=result.get("proposal_count", 0),
            )
        except Exception:
            logger.exception("scheduled_reflection_job_failed")


def start_scheduler() -> AsyncIOScheduler:
    """Idempotent: calling this more than once (e.g. test setup) returns the

    already-running scheduler rather than starting a second one.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _run_reflection_job,
        trigger=IntervalTrigger(hours=_interval_hours()),
        id="reflection_job",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("scheduler_started", interval_hours=_interval_hours())
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler
