from __future__ import annotations

from datetime import datetime, timezone

from app.database import Base
from sqlalchemy import JSON, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FeedbackRecord(Base):
    """One row per feedback signal on a runtime agent's output.

    INITIAL.md SELF-LEARNING SCOPE names two signal families: "User
    approve/reject/edit actions on drafts" (signal_type "approved" |
    "rejected" | "edited") and "actual engagement metrics measured 7 days
    post-publish" (signal_type "engagement_outcome", with the metrics in
    `engagement_stats`). Storage choice matches approval_request.py/
    agent_setting.py: Postgres via the shared Base/AsyncSession pattern, so
    feedback survives a process restart and the 7-day engagement lag is
    actually queryable later.
    """

    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    signal_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    detail: Mapped[str] = mapped_column(String, nullable=False, default="")
    engagement_stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
