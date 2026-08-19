"""Feedback capture — INITIAL.md SELF-LEARNING SCOPE's two feedback-signal

families: human approve/reject/edit decisions on drafts, and actual
engagement metrics measured 7 days post-publish. Storage only; the
reflection job (reflection_job.py) is what actually analyzes this data.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.models.feedback import FeedbackRecord
from app.tenancy.context import get_current_user_id
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Matches INITIAL.md exactly: "User approve/reject/edit actions on drafts;
# actual engagement metrics ... measured 7 days post-publish."
SIGNAL_APPROVED = "approved"
SIGNAL_REJECTED = "rejected"
SIGNAL_EDITED = "edited"
SIGNAL_ENGAGEMENT_OUTCOME = "engagement_outcome"

# "Negative" for reflection purposes: a human had to reject or correct
# something. "approved" is positive signal (nothing to reflect on), and
# engagement_outcome is analyzed separately (recent_engagement_outcomes),
# not as negative/positive.
_NEGATIVE_SIGNAL_TYPES = (SIGNAL_REJECTED, SIGNAL_EDITED)

DEFAULT_ENGAGEMENT_LAG_DAYS = 7


async def capture_feedback(
    db: AsyncSession,
    *,
    task_id: str,
    agent_name: str,
    signal_type: str,
    detail: str = "",
    engagement_stats: dict[str, Any] | None = None,
    confidence: float | None = None,
) -> FeedbackRecord:
    record = FeedbackRecord(
        id=str(uuid.uuid4()),
        user_id=get_current_user_id(),
        task_id=task_id,
        agent_name=agent_name,
        signal_type=signal_type,
        detail=detail,
        engagement_stats=engagement_stats,
        confidence=confidence,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def recent_negative_feedback(db: AsyncSession, days: int = 7) -> list[FeedbackRecord]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(FeedbackRecord)
        .where(
            FeedbackRecord.signal_type.in_(_NEGATIVE_SIGNAL_TYPES),
            FeedbackRecord.created_at >= cutoff,
            FeedbackRecord.user_id == get_current_user_id(),
        )
        .order_by(FeedbackRecord.created_at.desc())
    )
    return list(result.scalars().all())


async def recent_engagement_outcomes(db: AsyncSession, days: int = 7) -> list[FeedbackRecord]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(FeedbackRecord)
        .where(
            FeedbackRecord.signal_type == SIGNAL_ENGAGEMENT_OUTCOME,
            FeedbackRecord.created_at >= cutoff,
            FeedbackRecord.user_id == get_current_user_id(),
        )
        .order_by(FeedbackRecord.created_at.desc())
    )
    return list(result.scalars().all())
