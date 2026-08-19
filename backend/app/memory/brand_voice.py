"""Titled brand-voice profiles — the CRUD layer behind the dashboard's Brand

Voice section. Every write is dual: a BrandVoiceRecord (listable, titled,
editable) plus a RAG ingest (rag.ingest.ingest_style_guide, source_type=
"brand_voice") so the Content Writer's and Engagement Agent's own
search_knowledge_base/retrieve() calls actually surface it — same "internal
write record + RAG write, neither substitutes for the other" pattern as
research.write_research_note()'s save_research_note + ingest_research_note.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.brand_voice import BrandVoiceRecord
from app.rag.ingest import ingest_style_guide, remove_document
from app.tenancy.context import get_current_user_id
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_RAG_SOURCE_TYPE = "brand_voice"


async def create_brand_voice(
    db: AsyncSession, *, title: str, content: str, index_path: str | None = None
) -> BrandVoiceRecord:
    if not title.strip():
        raise ValueError("Brand voice title must be non-empty")
    if not content.strip():
        raise ValueError("Brand voice content must be non-empty")

    record = BrandVoiceRecord(id=str(uuid.uuid4()), user_id=get_current_user_id(), title=title, content=content)
    db.add(record)
    await db.commit()
    await db.refresh(record)

    await ingest_style_guide(
        doc_id=record.id,
        text=content,
        metadata={"title": title, "brand_voice_id": record.id},
        index_path=index_path,
    )
    return record


async def list_brand_voices(db: AsyncSession) -> list[BrandVoiceRecord]:
    result = await db.execute(
        select(BrandVoiceRecord)
        .where(BrandVoiceRecord.user_id == get_current_user_id())
        .order_by(BrandVoiceRecord.created_at.desc())
    )
    return list(result.scalars().all())


async def get_brand_voice(db: AsyncSession, brand_voice_id: str) -> BrandVoiceRecord | None:
    record = await db.get(BrandVoiceRecord, brand_voice_id)
    # A mismatched owner reads identically to "doesn't exist" — never confirms
    # to a caller that some other user's brand voice exists at all.
    if record is None or record.user_id != get_current_user_id():
        return None
    return record


async def update_brand_voice(
    db: AsyncSession, brand_voice_id: str, *, title: str, content: str, index_path: str | None = None
) -> BrandVoiceRecord | None:
    record = await db.get(BrandVoiceRecord, brand_voice_id)
    if record is None or record.user_id != get_current_user_id():
        return None
    if not title.strip():
        raise ValueError("Brand voice title must be non-empty")
    if not content.strip():
        raise ValueError("Brand voice content must be non-empty")

    record.title = title
    record.content = content
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(record)

    # ingest_style_guide() re-ingesting the same doc_id evicts the old
    # chunks first (VectorStore.upsert()'s idempotent-replace behavior) —
    # no separate remove_document() call needed for an edit, only a delete.
    await ingest_style_guide(
        doc_id=record.id,
        text=content,
        metadata={"title": title, "brand_voice_id": record.id},
        index_path=index_path,
    )
    return record


async def delete_brand_voice(db: AsyncSession, brand_voice_id: str, *, index_path: str | None = None) -> bool:
    record = await db.get(BrandVoiceRecord, brand_voice_id)
    if record is None or record.user_id != get_current_user_id():
        return False
    await db.delete(record)
    await db.commit()
    await remove_document(_RAG_SOURCE_TYPE, brand_voice_id, index_path=index_path)
    return True
