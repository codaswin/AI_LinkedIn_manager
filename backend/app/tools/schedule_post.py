from __future__ import annotations

import typing as t
import uuid
from datetime import datetime, timezone

from app.database import get_session_factory
from app.models.automation import ScheduledPostRecord
from app.tools.execution_context import current_idempotency_key
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


class SchedulePostArgs(BaseModel):
    content: str = Field(..., min_length=1)
    publish_at: datetime

    @field_validator("publish_at")
    @classmethod
    def _must_be_future(cls, value: datetime) -> datetime:
        reference = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if reference <= datetime.now(timezone.utc):
            raise ValueError("publish_at must be in the future")
        return reference.astimezone(timezone.utc)


@registry.register(
    ToolDefinition(
        name="schedule_post",
        description="Persist approved post content for automatic LinkedIn publishing at a future time",
        requires_approval=True,
    ),
    schema=SchedulePostArgs,
)
async def execute(args: SchedulePostArgs) -> dict[str, t.Any]:
    idempotency_key = current_idempotency_key()
    async with get_session_factory()() as db:
        if idempotency_key:
            existing = (
                await db.execute(
                    select(ScheduledPostRecord).where(
                        ScheduledPostRecord.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return _result(existing)

        record = ScheduledPostRecord(
            id=str(uuid.uuid4()),
            content=args.content,
            publish_at=args.publish_at,
            status="pending",
            idempotency_key=idempotency_key,
        )
        db.add(record)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            if not idempotency_key:
                raise
            existing = (
                await db.execute(
                    select(ScheduledPostRecord).where(
                        ScheduledPostRecord.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one()
            return _result(existing)
        await db.refresh(record)
        return _result(record)


def _result(record: ScheduledPostRecord) -> dict[str, t.Any]:
    return {
        "status": "scheduled",
        "scheduled_post_id": record.id,
        "content": record.content,
        "publish_at": record.publish_at.isoformat(),
    }
