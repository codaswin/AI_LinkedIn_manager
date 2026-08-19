from __future__ import annotations

from datetime import datetime, timezone

from app.database import Base
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PlatformCredentialRecord(Base):
    """One saved credential FIELD for one platform, owned by one dashboard user.

    A single-field platform (OpenAI's API key) is one row; a multi-field
    platform (Reddit's client ID + client secret + user agent) is three rows
    sharing a platform_id. `encrypted_value` is Fernet-encrypted (see
    app.safety.secrets) — never stored plaintext. `masked_preview` is
    computed once at save time so the read path (GET /credentials) never
    needs to decrypt just to show "last 4 characters" to the user.
    """

    __tablename__ = "platform_credentials"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # f"{user_id}:{platform_id}:{field_name}"
    user_id: Mapped[str] = mapped_column(ForeignKey("dashboard_users.id", ondelete="CASCADE"), nullable=False, index=True)
    platform_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String, nullable=False)
    encrypted_value: Mapped[str] = mapped_column(String, nullable=False)
    masked_preview: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
