from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SemanticMemoryRecord(Base):
    """Postgres metadata row paired 1:1 with a vector in the FAISS index (`vector_id` is the
    FAISS row offset). `source` and `confidence` are NOT NULL at the schema level as a second
    line of defense — the write-path validation in memory/semantic.py is the primary guard.
    """

    __tablename__ = "semantic_memory_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vector_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    entity_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String, nullable=False, index=True)
    fact: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
