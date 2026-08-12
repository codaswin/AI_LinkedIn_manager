from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PostEpisode(Base):
    """One row per published post, retained 12 months then archived (see memory/policy.py)."""

    __tablename__ = "post_episodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    post_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    content: Mapped[str] = mapped_column(String, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shares: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    follower_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ThreadEpisode(Base):
    """One row per comment/DM thread. `content` holds message text and is purged after 90 days
    unless `important` is set — metadata (participants, resolution, timestamps) is retained
    indefinitely for analytics/audit even after content purge, per LinkedIn ToS/privacy expectations.
    """

    __tablename__ = "thread_episodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    thread_type: Mapped[str] = mapped_column(String, nullable=False)  # "comment" | "dm"
    content: Mapped[str | None] = mapped_column(String, nullable=True)
    participants: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    resolution: Mapped[str | None] = mapped_column(String, nullable=True)  # auto_approved | human_edited | escalated
    important: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_purged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PostEpisodeCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    entity_id: str
    post_id: str
    content: str
    published_at: datetime
    impressions: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    follower_delta: int = 0


class ThreadEpisodeCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    entity_id: str
    thread_id: str
    thread_type: str = Field(pattern="^(comment|dm)$")
    content: str
    participants: list[str] = Field(default_factory=list)
    resolution: str | None = None
    important: bool = False
