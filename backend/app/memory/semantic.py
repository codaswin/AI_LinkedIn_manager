from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import faiss
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.semantic import SemanticMemoryRecord

_EMBEDDING_DIM = settings.RAG_EMBEDDING_DIM  # shared with rag-agent's embedder so vectors are dimension-compatible
_index_cache: dict[str, faiss.Index] = {}

# rag/ingest.py's VectorStore treats VECTOR_DB_PATH as a directory it owns (index.faiss +
# store.json live inside it). Semantic memory shares that same base directory but must not
# write a same-named file into it, so it gets its own filename within the shared directory.
_DEFAULT_INDEX_FILENAME = "semantic_memory.faiss"


def _default_index_path() -> str:
    return os.path.join(settings.VECTOR_DB_PATH, _DEFAULT_INDEX_FILENAME)


class MissingProvenanceError(ValueError):
    """Raised when a semantic memory write omits `source` or `confidence`.

    Forbidden outright per project rules: "Storing memory without a source and confidence field
    (untraceable memory is a liability)." This is enforced here, not just documented.
    """


def _embed(text: str) -> np.ndarray:
    # Deterministic hash-based bag-of-words embedding: a placeholder for the real embedding
    # model (`rag/ingest.embed`, not yet built) that keeps semantic memory self-contained and
    # testable without a live embedding endpoint, while satisfying the same write/search contract.
    vector = np.zeros(_EMBEDDING_DIM, dtype="float32")
    for token in text.lower().split():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % _EMBEDDING_DIM
        vector[idx] += 1.0
    norm = np.linalg.norm(vector)
    if norm > 0:
        vector /= norm
    return vector


def _load_index(index_path: str) -> faiss.Index:
    if index_path in _index_cache:
        return _index_cache[index_path]
    if os.path.exists(index_path):
        index = faiss.read_index(index_path)
    else:
        index = faiss.IndexIDMap(faiss.IndexFlatL2(_EMBEDDING_DIM))
    _index_cache[index_path] = index
    return index


def _save_index(index_path: str, index: faiss.Index) -> None:
    Path(index_path).parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, index_path)


async def _next_vector_id(db: AsyncSession) -> int:
    result = await db.execute(select(func.max(SemanticMemoryRecord.vector_id)))
    current_max = result.scalar()
    return 0 if current_max is None else current_max + 1


async def write(
    db: AsyncSession,
    *,
    entity_id: str,
    fact: str,
    source: str | None,
    confidence: float | None,
    category: str = "general",
    index_path: str | None = None,
) -> SemanticMemoryRecord:
    """Write a fact to semantic memory. `source` and `confidence` are mandatory and validated
    here — a write missing either is rejected, never silently defaulted."""
    if source is None or not str(source).strip():
        raise MissingProvenanceError("semantic memory write rejected: `source` is required")
    if confidence is None:
        raise MissingProvenanceError("semantic memory write rejected: `confidence` is required")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise MissingProvenanceError("semantic memory write rejected: `confidence` must be a float")
    if not (0.0 <= float(confidence) <= 1.0):
        raise MissingProvenanceError("semantic memory write rejected: `confidence` must be in [0.0, 1.0]")

    path = index_path or _default_index_path()
    next_id = await _next_vector_id(db)
    vector = await asyncio.to_thread(_embed, fact)

    def _add() -> None:
        index = _load_index(path)
        index.add_with_ids(vector.reshape(1, -1), np.array([next_id], dtype="int64"))
        _save_index(path, index)

    await asyncio.to_thread(_add)

    record = SemanticMemoryRecord(
        vector_id=next_id,
        entity_id=entity_id,
        category=category,
        fact=fact,
        source=source,
        confidence=float(confidence),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def search(
    db: AsyncSession,
    *,
    entity_id: str,
    query: str,
    top_k: int = 5,
    min_confidence: float = 0.5,
    category: str | None = None,
    index_path: str | None = None,
) -> list[SemanticMemoryRecord]:
    """Vector search filtered by entity, category, and a confidence floor — a low-confidence
    "fact" must never silently steer a new task, so it is excluded rather than merely ranked low."""
    path = index_path or _default_index_path()

    def _search_faiss() -> list[int]:
        index = _load_index(path)
        if index.ntotal == 0:
            return []
        vector = _embed(query).reshape(1, -1)
        k = min(top_k * 4, index.ntotal)
        _distances, ids = index.search(vector, k)
        return [int(i) for i in ids[0] if i != -1]

    candidate_ids = await asyncio.to_thread(_search_faiss)
    if not candidate_ids:
        return []

    stmt = select(SemanticMemoryRecord).where(
        SemanticMemoryRecord.vector_id.in_(candidate_ids),
        SemanticMemoryRecord.entity_id == entity_id,
        SemanticMemoryRecord.confidence >= min_confidence,
    )
    if category is not None:
        stmt = stmt.where(SemanticMemoryRecord.category == category)
    result = await db.execute(stmt)
    records = {r.vector_id: r for r in result.scalars().all()}
    ordered = [records[i] for i in candidate_ids if i in records]
    return ordered[:top_k]


def reset_index_cache() -> None:
    """Test-only helper: drops the in-process FAISS index cache so tests don't leak state
    across index_path fixtures."""
    _index_cache.clear()
