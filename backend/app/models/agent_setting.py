from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentSetting(Base):
    """API-readable settings store, e.g. research_agent.poll_interval, so cadence-style knobs
    are editable from a future dashboard UI without a code change or redeploy.

    Per-user composite `(user_id, key)` PK as of multi-tenant Stage 3 (see
    plans/peaceful-scribbling-tiger.md) — each dashboard user configures
    their own cadence/automation-topic settings independently, read/written
    via app.memory.settings.get_setting/set_setting.
    """

    __tablename__ = "agent_settings"

    user_id: Mapped[str] = mapped_column(ForeignKey("dashboard_users.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    updated_by: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
