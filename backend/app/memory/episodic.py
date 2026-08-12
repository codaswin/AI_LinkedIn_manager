from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.episode import PostEpisode, PostEpisodeCreate, ThreadEpisode, ThreadEpisodeCreate


async def record_post_episode(db: AsyncSession, data: PostEpisodeCreate) -> PostEpisode:
    episode = PostEpisode(**data.model_dump())
    db.add(episode)
    await db.commit()
    await db.refresh(episode)
    return episode


async def record_thread_episode(db: AsyncSession, data: ThreadEpisodeCreate) -> ThreadEpisode:
    episode = ThreadEpisode(**data.model_dump())
    db.add(episode)
    await db.commit()
    await db.refresh(episode)
    return episode


async def get_recent_posts(
    db: AsyncSession, entity_id: str, limit: int = 30, include_archived: bool = True
) -> list[PostEpisode]:
    stmt = select(PostEpisode).where(PostEpisode.entity_id == entity_id)
    if not include_archived:
        stmt = stmt.where(PostEpisode.archived.is_(False))
    stmt = stmt.order_by(PostEpisode.published_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_thread(db: AsyncSession, thread_id: str) -> ThreadEpisode | None:
    stmt = select(ThreadEpisode).where(ThreadEpisode.thread_id == thread_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_recent_threads(db: AsyncSession, entity_id: str, limit: int = 20) -> list[ThreadEpisode]:
    stmt = (
        select(ThreadEpisode)
        .where(ThreadEpisode.entity_id == entity_id)
        .order_by(ThreadEpisode.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def threads_with_stale_content(db: AsyncSession, cutoff: datetime) -> list[ThreadEpisode]:
    """Threads whose content is eligible for purge: older than `cutoff`, not already purged,
    and not flagged important by the user."""
    stmt = select(ThreadEpisode).where(
        ThreadEpisode.created_at < cutoff,
        ThreadEpisode.content_purged.is_(False),
        ThreadEpisode.important.is_(False),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def purge_thread_content(db: AsyncSession, thread_id: str, purged_at: datetime) -> None:
    """Deletes message content only — participants/resolution/timestamps (metadata) are kept
    so analytics and audit trails survive the privacy-driven content purge."""
    thread = await get_thread(db, thread_id)
    if thread is None:
        return
    thread.content = None
    thread.content_purged = True
    thread.content_purged_at = purged_at
    await db.commit()


async def posts_older_than(db: AsyncSession, cutoff: datetime) -> list[PostEpisode]:
    stmt = select(PostEpisode).where(PostEpisode.published_at < cutoff, PostEpisode.archived.is_(False))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def archive_post(db: AsyncSession, post_id: str) -> None:
    stmt = select(PostEpisode).where(PostEpisode.post_id == post_id)
    result = await db.execute(stmt)
    post = result.scalar_one_or_none()
    if post is None:
        return
    post.archived = True
    await db.commit()
