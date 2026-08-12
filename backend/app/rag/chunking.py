import re
from typing import Any, Literal

from pydantic import BaseModel, Field

SourceType = Literal[
    "past_posts",
    "brand_voice",
    "industry_news",
    "comment_dm_threads",
    "x_research_notes",
]

SOURCE_TYPES: tuple[SourceType, ...] = (
    "past_posts",
    "brand_voice",
    "industry_news",
    "comment_dm_threads",
    "x_research_notes",
)


class Chunk(BaseModel):
    id: str
    text: str
    source_type: SourceType
    source_id: str
    chunk_index: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


_CHARS_PER_TOKEN = 4
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def chunk_structured_1to1(
    source_id: str,
    source_type: SourceType,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> list[Chunk]:
    """Sources 1/4/5: one post, thread, or research note maps to exactly one chunk."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("cannot chunk empty text")
    return [
        Chunk(
            id=f"{source_type}:{source_id}",
            text=stripped,
            source_type=source_type,
            source_id=source_id,
            chunk_index=0,
            metadata=metadata or {},
        )
    ]


def _split_oversized_paragraph(paragraph: str, target_chars: int) -> list[str]:
    sentences = _SENTENCE_SPLIT_RE.split(paragraph)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > target_chars:
            if current:
                pieces.append(current.strip())
                current = ""
            for i in range(0, len(sentence), target_chars):
                pieces.append(sentence[i : i + target_chars].strip())
            continue
        if current and len(current) + len(sentence) + 1 > target_chars:
            pieces.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current.strip())
    return [p for p in pieces if p]


def chunk_document_semantic(
    source_id: str,
    source_type: SourceType,
    text: str,
    target_tokens: int = 500,
    overlap_tokens: int = 50,
    metadata: dict[str, Any] | None = None,
) -> list[Chunk]:
    """Sources 2/3: split on paragraph (semantic) boundaries, not fixed-width slicing.

    Paragraphs are merged until the target token budget is hit; a paragraph that
    alone exceeds the budget is broken on sentence boundaries (falling back to a
    hard slice only for a single run-on sentence longer than the whole budget).
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("cannot chunk empty text")

    target_chars = target_tokens * _CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * _CHARS_PER_TOKEN

    raw_paragraphs = [p.strip() for p in stripped.split("\n\n") if p.strip()]
    paragraphs: list[str] = []
    for para in raw_paragraphs:
        if len(para) > target_chars:
            paragraphs.extend(_split_oversized_paragraph(para, target_chars))
        else:
            paragraphs.append(para)

    segments: list[str] = []
    current = ""
    for para in paragraphs:
        if current and len(current) + len(para) + 2 > target_chars:
            segments.append(current)
            current = (current[-overlap_chars:] + "\n\n" + para) if overlap_chars else para
        else:
            current = f"{current}\n\n{para}".strip() if current else para
    if current:
        segments.append(current)

    base_metadata = metadata or {}
    return [
        Chunk(
            id=f"{source_type}:{source_id}:{i}",
            text=segment.strip(),
            source_type=source_type,
            source_id=source_id,
            chunk_index=i,
            metadata=base_metadata,
        )
        for i, segment in enumerate(segments)
    ]
