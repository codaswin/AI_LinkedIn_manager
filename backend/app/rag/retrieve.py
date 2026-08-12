import asyncio
from typing import Any, get_args

from app.config import settings
from app.rag.chunking import SourceType
from app.rag.ingest import VectorStore, embed_texts

_VALID_SOURCE_TYPES = set(get_args(SourceType))


def _resolve_path(index_path: str | None) -> str:
    return index_path or settings.VECTOR_DB_PATH


def _retrieve_sync(
    query: str, source_types: list[str] | None, top_k: int, index_path: str
) -> list[dict[str, Any]]:
    store = VectorStore(index_path)
    query_vector = embed_texts([query])
    hits = store.search(query_vector, k=store.count())

    results: list[dict[str, Any]] = []
    for vector_id, score in hits:
        chunk = store.get_chunk(vector_id)
        if chunk is None:
            continue
        if source_types is not None and chunk["source_type"] not in source_types:
            continue
        results.append({**chunk, "score": score})
        if len(results) >= top_k:
            break
    return results


async def retrieve(
    query: str,
    source_types: list[str] | None = None,
    top_k: int = 5,
    *,
    index_path: str | None = None,
) -> list[dict[str, Any]]:
    """Filter by one or more of the 5 source types, or pass None to search all.

    e.g. the Content Writer Agent queries only ["brand_voice", "past_posts"]
    while the Research Agent queries only ["industry_news"] to dedupe against
    its own X findings.
    """
    if source_types is not None:
        invalid = set(source_types) - _VALID_SOURCE_TYPES
        if invalid:
            raise ValueError(f"Unknown source_types: {sorted(invalid)}")

    return await asyncio.to_thread(
        _retrieve_sync, query, source_types, top_k, _resolve_path(index_path)
    )
