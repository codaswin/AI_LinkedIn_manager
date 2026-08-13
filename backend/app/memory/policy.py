from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from .episodic import (
    archive_post,
    posts_older_than,
    purge_thread_content,
    threads_with_stale_content,
)

POST_RETENTION = timedelta(days=settings.EPISODIC_POST_RETENTION_DAYS)
THREAD_CONTENT_RETENTION = timedelta(days=settings.EPISODIC_THREAD_CONTENT_RETENTION_DAYS)


async def purge_stale_thread_content(db: AsyncSession, now: datetime | None = None) -> int:
    """Purges comment/DM thread *content* older than 90 days, per LinkedIn ToS/privacy
    expectations — third-party message text shouldn't be retained indefinitely. Metadata
    (participants, resolution, created_at) is left intact for audit/analytics, and threads the
    user flagged `important` are exempt from purge entirely."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - THREAD_CONTENT_RETENTION
    stale = await threads_with_stale_content(db, cutoff)
    for thread in stale:
        await purge_thread_content(db, thread.thread_id, purged_at=now)
    return len(stale)


async def archive_old_posts(db: AsyncSession, now: datetime | None = None) -> int:
    """Marks published posts older than 12 months as archived. Archival keeps the row (engagement
    stats stay queryable for long-run analytics) rather than deleting it — unlike thread content,
    post/engagement data carries no third-party message text, so there is no privacy driver to purge it."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - POST_RETENTION
    stale = await posts_older_than(db, cutoff)
    for post in stale:
        await archive_post(db, post.post_id)
    return len(stale)


async def run_retention_purge(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """The scheduled purge job: run both retention rules and report what changed."""
    threads_purged = await purge_stale_thread_content(db, now)
    posts_archived = await archive_old_posts(db, now)
    return {"threads_content_purged": threads_purged, "posts_archived": posts_archived}
