import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from app.config import settings
from app.rag.chunking import Chunk, chunk_document_semantic, chunk_structured_1to1

EMBEDDING_DIM = settings.RAG_EMBEDDING_DIM

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def embed_texts(texts: list[str]) -> np.ndarray:
    """Deterministic, offline hashing-trick embedding.

    No embedding client exists yet in this fresh backend (that's the
    llmops-agent's job in a later phase), and CLAUDE.md forbids calling any
    LLM/embedding API directly from this layer anyway. A local hashed
    bag-of-words vector keeps ingest/retrieve fully offline and testable now;
    swapping in a real embedding client later only touches this function.
    """
    vectors = np.zeros((len(texts), EMBEDDING_DIM), dtype="float32")
    for row, text in enumerate(texts):
        for token in _tokenize(text):
            bucket = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % EMBEDDING_DIM
            vectors[row, bucket] += 1.0
    faiss.normalize_L2(vectors)
    return vectors


def stable_id_to_int(chunk_id: str) -> int:
    digest = hashlib.sha1(chunk_id.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big", signed=False) & 0x7FFFFFFFFFFFFFFF


class VectorStore:
    """Shared FAISS-backed vector store all five ingest_* functions write into."""

    def __init__(self, index_path: str, dim: int = EMBEDDING_DIM) -> None:
        self.index_path = Path(index_path)
        self.dim = dim
        self.index_file = self.index_path / "index.faiss"
        self.meta_file = self.index_path / "store.json"
        self.index_path.mkdir(parents=True, exist_ok=True)
        self._index, self._chunks, self._registry = self._load()

    def _load(self) -> tuple[faiss.Index, dict[int, dict[str, Any]], dict[str, list[int]]]:
        if self.index_file.exists() and self.meta_file.exists():
            index = faiss.read_index(str(self.index_file))
            data = json.loads(self.meta_file.read_text())
            chunks = {int(k): v for k, v in data.get("chunks", {}).items()}
            registry = data.get("doc_registry", {})
        else:
            index = faiss.IndexIDMap2(faiss.IndexFlatIP(self.dim))
            chunks = {}
            registry = {}
        return index, chunks, registry

    def _save(self) -> None:
        faiss.write_index(self._index, str(self.index_file))
        data = {
            "chunks": {str(k): v for k, v in self._chunks.items()},
            "doc_registry": self._registry,
        }
        self.meta_file.write_text(json.dumps(data))

    def upsert(self, chunks: list[Chunk]) -> int:
        """Idempotent by (source_type, source_id): re-ingesting the same stable
        id first evicts that document's previously written chunks (there may be
        a different count than before, e.g. an edited article) before adding
        the fresh ones, so the index never accumulates duplicates."""
        if not chunks:
            return 0

        doc_keys = {f"{c.source_type}:{c.source_id}" for c in chunks}
        for key in doc_keys:
            existing_ids = self._registry.get(key, [])
            if existing_ids:
                self._index.remove_ids(np.array(existing_ids, dtype="int64"))
                for eid in existing_ids:
                    self._chunks.pop(eid, None)
            self._registry[key] = []

        vectors = embed_texts([c.text for c in chunks])
        ids = np.array([stable_id_to_int(c.id) for c in chunks], dtype="int64")
        self._index.add_with_ids(vectors, ids)

        for chunk, vid in zip(chunks, ids.tolist()):
            self._chunks[vid] = chunk.model_dump()
            key = f"{chunk.source_type}:{chunk.source_id}"
            self._registry.setdefault(key, []).append(vid)

        self._save()
        return len(chunks)

    def remove(self, source_type: str, source_id: str) -> int:
        """Evict a document's chunks without re-adding anything — the same

        removal half of upsert()'s idempotent-replace logic, exposed
        standalone for callers that delete a source document outright
        (e.g. memory.brand_voice.delete_brand_voice()) rather than replacing
        it. Returns the number of chunks removed.
        """
        key = f"{source_type}:{source_id}"
        existing_ids = self._registry.get(key, [])
        if not existing_ids:
            return 0
        self._index.remove_ids(np.array(existing_ids, dtype="int64"))
        for eid in existing_ids:
            self._chunks.pop(eid, None)
        self._registry[key] = []
        self._save()
        return len(existing_ids)

    def search(self, query_vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self._index.ntotal == 0:
            return []
        k = min(k, self._index.ntotal)
        scores, ids = self._index.search(query_vector, k)
        return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i != -1]

    def get_chunk(self, vector_id: int) -> dict[str, Any] | None:
        return self._chunks.get(vector_id)

    def count(self) -> int:
        return self._index.ntotal


def _resolve_path(index_path: str | None) -> str:
    return index_path or settings.VECTOR_DB_PATH


def _upsert_sync(chunks: list[Chunk], index_path: str) -> int:
    return VectorStore(index_path).upsert(chunks)


async def _upsert(chunks: list[Chunk], index_path: str | None) -> int:
    return await asyncio.to_thread(_upsert_sync, chunks, _resolve_path(index_path))


def _remove_sync(source_type: str, source_id: str, index_path: str) -> int:
    return VectorStore(index_path).remove(source_type, source_id)


async def remove_document(source_type: str, source_id: str, *, index_path: str | None = None) -> int:
    """Evict one previously-ingested document's chunks. Returns the number removed."""
    return await asyncio.to_thread(_remove_sync, source_type, source_id, _resolve_path(index_path))


async def ingest_post(
    post_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    *,
    index_path: str | None = None,
) -> int:
    chunks = chunk_structured_1to1(
        source_id=post_id, source_type="past_posts", text=text, metadata=metadata
    )
    return await _upsert(chunks, index_path)


async def ingest_style_guide(
    doc_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    *,
    target_tokens: int = 500,
    index_path: str | None = None,
) -> int:
    chunks = chunk_document_semantic(
        source_id=doc_id,
        source_type="brand_voice",
        text=text,
        target_tokens=target_tokens,
        metadata=metadata,
    )
    return await _upsert(chunks, index_path)


async def ingest_news_article(
    article_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    *,
    target_tokens: int = 500,
    index_path: str | None = None,
) -> int:
    chunks = chunk_document_semantic(
        source_id=article_id,
        source_type="industry_news",
        text=text,
        target_tokens=target_tokens,
        metadata=metadata,
    )
    return await _upsert(chunks, index_path)


async def ingest_thread(
    thread_id: str,
    question: str,
    answer: str,
    metadata: dict[str, Any] | None = None,
    *,
    index_path: str | None = None,
) -> int:
    text = f"Q: {question}\nA: {answer}"
    merged_metadata = {**(metadata or {}), "question": question, "answer": answer}
    chunks = chunk_structured_1to1(
        source_id=thread_id,
        source_type="comment_dm_threads",
        text=text,
        metadata=merged_metadata,
    )
    return await _upsert(chunks, index_path)


async def ingest_research_note(
    note_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    *,
    index_path: str | None = None,
) -> int:
    """Called directly by the Research Agent whenever it produces a note.

    Poll cadence (default daily) is a runtime setting in the memory layer's
    agent_settings table, owned by the Research Agent/scheduler — not this
    module's concern, so no scheduling logic lives here.
    """
    chunks = chunk_structured_1to1(
        source_id=note_id, source_type="x_research_notes", text=text, metadata=metadata
    )
    return await _upsert(chunks, index_path)
