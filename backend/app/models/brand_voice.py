from __future__ import annotations

from datetime import datetime, timezone

from app.database import Base
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BrandVoiceRecord(Base):
    """A titled brand-voice/style-guide profile.

    Dual-write pattern (see memory/brand_voice.py), same rationale as
    research.write_research_note()'s save_research_note + ingest_research_note
    pair: this table is the listable/titled record a UI shows, while the RAG
    ingest (rag.ingest.ingest_style_guide, source_type="brand_voice") is what
    actually makes the content retrievable by the Content Writer/Engagement
    agents via search_knowledge_base. Neither write substitutes for the other.
    """

    __tablename__ = "brand_voices"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
