from __future__ import annotations

from datetime import datetime, timezone

from app.database import Base
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ScheduledPostRecord(Base):
    __tablename__ = "scheduled_posts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Stamped by tools/schedule_post.py on every insert; process_due_posts()
    # (app.automation) is always called with a specific user_id by the
    # scheduler's per-user fan-out (Stage 3, plans/peaceful-scribbling-tiger.md).
    user_id: Mapped[str] = mapped_column(ForeignKey("dashboard_users.id", ondelete="CASCADE"), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    publish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True, unique=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessedNotificationRecord(Base):
    __tablename__ = "processed_notifications"

    notification_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Stamped by learning/scheduler.py's per-user engagement job (Stage 3).
    user_id: Mapped[str] = mapped_column(ForeignKey("dashboard_users.id", ondelete="CASCADE"), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
