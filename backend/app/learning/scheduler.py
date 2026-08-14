"""Runtime automation jobs for reflection, research, engagement, retention, and scheduled publishing."""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from functools import partial
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from redis.exceptions import WatchError
from sqlalchemy.exc import IntegrityError

from app.shared_state import get_client

logger = structlog.get_logger(__name__)

_DEFAULT_REFLECTION_HOURS = 24.0 * 7
_scheduler: AsyncIOScheduler | None = None
_scheduler_owner_id = str(uuid.uuid4())
_SCHEDULER_OWNER_KEY = "runtime:scheduler-owner"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


def _interval_hours() -> float:
    return _env_float("REFLECTION_JOB_INTERVAL_HOURS", _DEFAULT_REFLECTION_HOURS)


async def _run_reflection_job() -> None:
    from app.database import get_session_factory
    from app.learning.feedback import DEFAULT_ENGAGEMENT_LAG_DAYS
    from app.learning.reflection_job import run_reflection
    from app.llmops.model_router import route_and_call

    async with get_session_factory()() as db:
        try:
            result = await run_reflection(db, route_and_call, days=DEFAULT_ENGAGEMENT_LAG_DAYS)
            logger.info(
                "scheduled_reflection_job_completed",
                **{
                    "ran": result["ran"],
                    "feedback_count": result["feedback_count"],
                    "proposal_count": result.get("proposal_count", 0),
                },
            )
        except Exception:
            logger.exception("scheduled_reflection_job_failed")


async def _run_retention_job() -> None:
    from app.database import get_session_factory
    from app.memory.policy import run_retention_purge

    async with get_session_factory()() as db:
        try:
            result = await run_retention_purge(db)
            logger.info("scheduled_retention_job_completed", **result)
        except Exception:
            logger.exception("scheduled_retention_job_failed")


async def _run_scheduled_posts_job() -> None:
    from app.automation import process_due_posts

    try:
        result = await process_due_posts()
        if result["claimed"]:
            logger.info("scheduled_posts_job_completed", **result)
    except Exception:
        logger.exception("scheduled_posts_job_failed")


def _notification_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("notifications", "items", "data"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []


async def _run_engagement_job() -> None:
    from app.agents.engagement import handle_notification
    from app.database import get_session_factory
    from app.llmops.model_router import route_and_call
    from app.models.automation import ProcessedNotificationRecord
    from app.tools.registry import execute_tool

    result = await execute_tool(
        "get_linkedin_notifications",
        {"limit": int(os.environ.get("ENGAGEMENT_POLL_LIMIT", "20"))},
        approved=False,
    )
    if result.get("status") != "success":
        logger.warning("engagement_poll_failed", result=result)
        return
    notifications = _notification_list((result.get("result") or {}).get("notifications"))
    factory = get_session_factory()
    processed = 0
    for notification in notifications:
        notification_id = str(notification.get("id") or "").strip()
        if not notification_id or notification.get("type") not in {"comment", "dm", "connection_request"}:
            continue
        async with factory() as db:
            if await db.get(ProcessedNotificationRecord, notification_id) is not None:
                continue
            db.add(ProcessedNotificationRecord(notification_id=notification_id, outcome="processing"))
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                continue
            try:
                outcome = await handle_notification(notification, route_and_call, db=db)
                record = await db.get(ProcessedNotificationRecord, notification_id)
                if record is not None:
                    record.outcome = str(outcome.get("status", "completed"))
                await db.commit()
                processed += 1
            except Exception:
                await db.rollback()
                record = await db.get(ProcessedNotificationRecord, notification_id)
                if record is not None:
                    await db.delete(record)
                    await db.commit()
                logger.exception("engagement_notification_failed", notification_id=notification_id)
    if processed:
        logger.info("engagement_poll_completed", processed=processed)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def _run_research_job() -> None:
    from app.agents.research import get_poll_interval, poll_interval_to_timedelta
    from app.agents.research_pipeline import conduct_research
    from app.database import get_session_factory
    from app.llmops.model_router import route_and_call
    from app.memory.settings import get_setting, set_setting

    now = datetime.now(timezone.utc)
    async with get_session_factory()() as db:
        interval = poll_interval_to_timedelta(await get_poll_interval(db))
        last_raw = await get_setting(db, "research_agent.last_run_at")
        if last_raw:
            try:
                if now - _as_utc(datetime.fromisoformat(last_raw)) < interval:
                    return
            except ValueError:
                logger.warning("research_last_run_invalid", value=last_raw)

        queries = [
            q.strip()
            for q in os.environ.get("RESEARCH_AUTOMATION_QUERIES", "AI agents,agentic AI,AI tooling").split(",")
            if q.strip()
        ]
        try:
            for query in queries:
                await conduct_research(query, route_and_call, persist=True)
            await set_setting(db, "research_agent.last_run_at", now.isoformat(), updated_by="system:scheduler")
            logger.info("scheduled_research_job_completed", query_count=len(queries))
        except Exception:
            logger.exception("scheduled_research_job_failed")


def _owns_scheduler(client: Any) -> bool:
    lease_seconds = int(_env_float("SCHEDULER_OWNER_LEASE_SECONDS", 120))
    if client.set(_SCHEDULER_OWNER_KEY, _scheduler_owner_id, nx=True, ex=lease_seconds):
        return True
    if client.get(_SCHEDULER_OWNER_KEY) == _scheduler_owner_id:
        client.expire(_SCHEDULER_OWNER_KEY, lease_seconds)
        return True
    return False


async def _run_distributed_job(job_id: str, fn: Callable[[], Awaitable[None]], lock_seconds: int) -> None:
    """Run one scheduled job globally, even when every worker owns a scheduler."""
    client = get_client()
    if not _owns_scheduler(client):
        logger.info("scheduler_job_skipped_not_owner", job_id=job_id)
        return
    key = f"runtime:scheduler-lock:{job_id}"
    owner = str(uuid.uuid4())
    if not client.set(key, owner, nx=True, ex=lock_seconds):
        logger.info("scheduler_job_skipped_lock_held", job_id=job_id)
        return
    try:
        await fn()
    finally:
        while True:
            try:
                with client.pipeline() as pipe:
                    pipe.watch(key)
                    if pipe.get(key) != owner:
                        pipe.unwatch()
                        break
                    pipe.multi()
                    pipe.delete(key)
                    pipe.execute()
                    break
            except WatchError:
                continue


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = AsyncIOScheduler()
    jobs = (
        (_run_reflection_job, IntervalTrigger(hours=_interval_hours()), "reflection_job"),
        (_run_research_job, IntervalTrigger(minutes=_env_float("RESEARCH_SCHEDULER_TICK_MINUTES", 15)), "research_job"),
        (
            _run_engagement_job,
            IntervalTrigger(minutes=_env_float("ENGAGEMENT_POLL_INTERVAL_MINUTES", 5)),
            "engagement_job",
        ),
        (_run_retention_job, IntervalTrigger(hours=_env_float("RETENTION_JOB_INTERVAL_HOURS", 24)), "retention_job"),
        (
            _run_scheduled_posts_job,
            IntervalTrigger(seconds=_env_float("SCHEDULED_POST_POLL_SECONDS", 60)),
            "scheduled_posts_job",
        ),
    )
    lock_seconds = int(_env_float("SCHEDULER_JOB_LOCK_SECONDS", 3600))
    for fn, trigger, job_id in jobs:
        distributed_fn = partial(_run_distributed_job, job_id, fn, lock_seconds)
        _scheduler.add_job(
            distributed_fn, trigger=trigger, id=job_id, replace_existing=True, max_instances=1, coalesce=True
        )
    _scheduler.start()
    logger.info("scheduler_started", job_count=len(jobs))
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        client = get_client()
        if client.get(_SCHEDULER_OWNER_KEY) == _scheduler_owner_id:
            client.delete(_SCHEDULER_OWNER_KEY)


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler
