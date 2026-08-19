from __future__ import annotations

from datetime import datetime, timezone

from app.database import Base
from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LearningProposalRecord(Base):
    """One row per change the reflection job proposes — mirrors

    ApprovalRequestRecord's shape closely (same survive-a-restart rationale:
    a human may review this minutes or days after the reflection job that
    proposed it has exited), but status has a fourth value ("auto_applied")
    that ApprovalRequestRecord doesn't need, since some proposal types are
    allowed to skip human review (proposal_review.AUTO_APPLY_TYPES).
    """

    __tablename__ = "learning_proposals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Stamped by learning/proposal_review.py's submit_proposal on every
    # insert (Stage 3, plans/peaceful-scribbling-tiger.md).
    user_id: Mapped[str] = mapped_column(ForeignKey("dashboard_users.id", ondelete="CASCADE"), nullable=False, index=True)
    pattern: Mapped[str] = mapped_column(String, nullable=False)
    change_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    proposed_change: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
