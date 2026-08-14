from __future__ import annotations

from datetime import datetime, timezone
import uuid

from app.database import Base
from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalRequestRecord(Base):
    """One row per `requires_approval` tool call queued for human review.

    Storage choice: Postgres via the shared `Base`/`AsyncSession` pattern already
    established by app/models/agent_setting.py and app/models/episode.py (SQLite
    in-memory in tests, per conftest.py's db_session fixture) rather than an
    in-memory dict — an approval request must survive a process restart, since a
    human may approve/reject minutes or hours after the agent that submitted the
    request has exited.
    """

    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tool_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    arguments: Mapped[dict] = mapped_column(JSON, nullable=False)
    requested_by_agent: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    idempotency_key: Mapped[str] = mapped_column(
        String, nullable=False, unique=True, index=True, default=lambda: str(uuid.uuid4())
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApprovalExecutionAttemptRecord(Base):
    __tablename__ = "approval_execution_attempts"
    __table_args__ = (UniqueConstraint("approval_id", "attempt_number"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    approval_id: Mapped[str] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
