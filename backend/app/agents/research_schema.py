"""Normalized schema every research source's results are mapped into.

The Researcher Agent (and everything downstream — dedup, ranking,
synthesis, the Content Writer) works against ResearchResult only. No
caller reaches into a source-specific raw shape (an HN item, a Reddit
listing child, a GitHub repo object, ...) past its own adapter in
research_sources.py — that's what "do not tightly couple the Researcher
Agent to any specific source" means in practice.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# Kept as a plain list (not a StrEnum) so a new source can be added to
# research_sources.SOURCE_ADAPTERS without also having to touch an enum
# definition here — the set of *valid* names for validation purposes is
# research_sources.ALL_SOURCES, not this schema.
SourceName = str


class ResearchResult(BaseModel):
    source: SourceName
    title: str
    url: str
    content: str = ""
    author: str | None = None
    published_at: datetime | None = None
    engagement: dict[str, int | float] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    relevance_score: float = 0.0


class ResearchPackage(BaseModel):
    """What the Researcher Agent hands the Content Strategist/Writer.

    `citations` is built by code directly from `source_results` URLs, never
    written by the LLM — the synthesis step's prompt only ever asks for
    executive_summary/key_findings/interesting_angles, so there is no path
    by which a citation could be fabricated (PRP "Research Output": "Do not
    fabricate citations or URLs").
    """

    research_query: str
    executive_summary: str
    key_findings: list[str]
    interesting_angles: list[str]
    source_results: list[ResearchResult]
    citations: list[str]
    source_coverage: dict[str, int]
    research_timestamp: datetime
