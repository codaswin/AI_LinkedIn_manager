from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from app.database import get_session_factory
from app.models.automation import ScheduledPostRecord
from app.tools.publish_post import publish_content
from sqlalchemy import select, update

logger = structlog.get_logger(__name__)


async def process_due_posts(now: datetime | None = None, limit: int = 20) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(
            select(ScheduledPostRecord.id)
            .where(ScheduledPostRecord.status == "pending", ScheduledPostRecord.publish_at <= now)
            .order_by(ScheduledPostRecord.publish_at)
            .limit(limit)
        )
        candidate_ids = list(result.scalars())

    claimed_count = published = failed = 0
    for post_id in candidate_ids:
        async with factory() as db:
            claimed = await db.execute(
                update(ScheduledPostRecord)
                .where(ScheduledPostRecord.id == post_id, ScheduledPostRecord.status == "pending")
                .values(status="publishing", attempts=ScheduledPostRecord.attempts + 1)
            )
            await db.commit()
            if claimed.rowcount != 1:
                continue
            claimed_count += 1
            record = await db.get(ScheduledPostRecord, post_id)
            if record is None:
                continue
            content = record.content

        try:
            publish_result: dict[str, Any] = await publish_content(content)
        except Exception as exc:
            async with factory() as db:
                record = await db.get(ScheduledPostRecord, post_id)
                if record is not None:
                    record.status = "failed"
                    record.last_error = str(exc)
                    await db.commit()
            failed += 1
            logger.exception("scheduled_post_failed", scheduled_post_id=post_id)
        else:
            async with factory() as db:
                record = await db.get(ScheduledPostRecord, post_id)
                if record is not None:
                    record.status = "published"
                    record.published_at = datetime.now(timezone.utc)
                    record.last_error = None
                    await db.commit()
            published += 1
            logger.info("scheduled_post_published", scheduled_post_id=post_id, result=publish_result)

    return {"claimed": claimed_count, "published": published, "failed": failed}
