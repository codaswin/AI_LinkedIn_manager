import asyncio
import fcntl
import os
import threading
import uuid
import hashlib
import json
import re
from contextlib import contextmanager
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


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


class VectorStore:
    """FAISS store persisted as atomically-selected, lock-protected generations."""

    def __init__(self, index_path: str, dim: int = EMBEDDING_DIM) -> None:
        self.index_path = Path(index_path)
        self.dim = dim
        self.current_file = self.index_path / "CURRENT"
        self.lock_file = self.index_path / ".store.lock"
        self.legacy_index_file = self.index_path / "index.faiss"
        self.legacy_meta_file = self.index_path / "store.json"
        self.index_path.mkdir(parents=True, exist_ok=True)
        with self._locked():
            self._index, self._chunks, self._registry = self._load()

    @contextmanager
    def _locked(self):
        with _thread_lock(self.index_path):
            with self.lock_file.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _empty(self) -> tuple[faiss.Index, dict[int, dict[str, Any]], dict[str, list[int]]]:
        return faiss.IndexIDMap2(faiss.IndexFlatIP(self.dim)), {}, {}

    def _load_pair(self, index_file: Path, meta_file: Path):
        index = faiss.read_index(str(index_file))
        data = json.loads(meta_file.read_text())
        if index.d != self.dim:
            raise ValueError(f"FAISS index dimension {index.d} does not match configured dimension {self.dim}")
        chunks = {int(k): v for k, v in data.get("chunks", {}).items()}
        registry = data.get("doc_registry", {})
        if index.ntotal != len(chunks):
            raise ValueError("FAISS index and metadata snapshot contain different item counts")
        return index, chunks, registry

    def _load(self) -> tuple[faiss.Index, dict[int, dict[str, Any]], dict[str, list[int]]]:
        if self.current_file.exists():
            generation = self.current_file.read_text().strip()
            if generation:
                index_file = self.index_path / f"index.{generation}.faiss"
                meta_file = self.index_path / f"store.{generation}.json"
                if index_file.exists() and meta_file.exists():
                    return self._load_pair(index_file, meta_file)
        if self.legacy_index_file.exists() and self.legacy_meta_file.exists():
            return self._load_pair(self.legacy_index_file, self.legacy_meta_file)
        return self._empty()

    @staticmethod
    def _fsync(path: Path) -> None:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())

    def _save(self) -> None:
        generation = uuid.uuid4().hex
        index_tmp = self.index_path / f".index.{generation}.tmp"
        meta_tmp = self.index_path / f".store.{generation}.tmp"
        index_final = self.index_path / f"index.{generation}.faiss"
        meta_final = self.index_path / f"store.{generation}.json"
        current_tmp = self.index_path / ".CURRENT.tmp"

        faiss.write_index(self._index, str(index_tmp))
        meta_tmp.write_text(json.dumps({
            "generation": generation,
            "chunks": {str(k): v for k, v in self._chunks.items()},
            "doc_registry": self._registry,
        }))
        self._fsync(index_tmp)
        self._fsync(meta_tmp)
        os.replace(index_tmp, index_final)
        os.replace(meta_tmp, meta_final)

        current_tmp.write_text(generation)
        self._fsync(current_tmp)
        os.replace(current_tmp, self.current_file)

        for path in self.index_path.glob("index.*.faiss"):
            if path != index_final:
                path.unlink(missing_ok=True)
        for path in self.index_path.glob("store.*.json"):
            if path != meta_final:
                path.unlink(missing_ok=True)

    def upsert(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        with self._locked():
            self._index, self._chunks, self._registry = self._load()
            doc_keys = {f"{chunk.source_type}:{chunk.source_id}" for chunk in chunks}
            for key in doc_keys:
                existing_ids = self._registry.get(key, [])
                if existing_ids:
                    self._index.remove_ids(np.array(existing_ids, dtype="int64"))
                    for existing_id in existing_ids:
                        self._chunks.pop(existing_id, None)
                self._registry[key] = []

            vectors = embed_texts([chunk.text for chunk in chunks])
            ids = np.array([stable_id_to_int(chunk.id) for chunk in chunks], dtype="int64")
            self._index.add_with_ids(vectors, ids)
            for chunk, vector_id in zip(chunks, ids.tolist()):
                self._chunks[vector_id] = chunk.model_dump()
                key = f"{chunk.source_type}:{chunk.source_id}"
                self._registry.setdefault(key, []).append(vector_id)
            self._save()
        return len(chunks)

    def remove(self, source_type: str, source_id: str) -> int:
        with self._locked():
            self._index, self._chunks, self._registry = self._load()
            key = f"{source_type}:{source_id}"
            existing_ids = self._registry.get(key, [])
            if not existing_ids:
                return 0
            self._index.remove_ids(np.array(existing_ids, dtype="int64"))
            for existing_id in existing_ids:
                self._chunks.pop(existing_id, None)
            self._registry[key] = []
            self._save()
            return len(existing_ids)

    def search(self, query_vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        with self._locked():
            self._index, self._chunks, self._registry = self._load()
            if self._index.ntotal == 0:
                return []
            k = min(k, self._index.ntotal)
            scores, ids = self._index.search(query_vector, k)
            return [(int(i), float(score)) for i, score in zip(ids[0], scores[0]) if i != -1]

    def get_chunk(self, vector_id: int) -> dict[str, Any] | None:
        with self._locked():
            self._index, self._chunks, self._registry = self._load()
            return self._chunks.get(vector_id)

    def count(self) -> int:
        with self._locked():
            self._index, self._chunks, self._registry = self._load()
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
