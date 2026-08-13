"""Multi-source research orchestration: select -> fetch (parallel) -> dedupe

-> rank -> synthesize -> persist. This is what turns the six-plus
independent ResearchSource adapters in research_sources.py into the single
ResearchPackage the Content Strategist/Writer consume.

CLAUDE.md non-negotiable #1: the only LLM call in this module (the
synthesis step) goes through harness.loop.run_step(), exactly like
research.py's existing triage_x_posts()/write_research_note().
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import structlog
from app.activity import activity
from app.agents.research_schema import ResearchPackage, ResearchResult
from app.agents.research_sources import ALL_SOURCES
from app.harness.loop import AgentRunConfig, LLMClient, run_step
from app.harness.state import AgentState, RuntimeAgentName
from app.llmops.model_router import route
from app.rag.ingest import ingest_research_note
from app.tools.registry import execute_tool

logger = structlog.get_logger(__name__)

# "Maximum results per source" / "maximum overall research budget" (PRP
# Reliability requirements) — plain caps, not configurable per-call beyond
# these keyword args, so a research run can never fan out unboundedly.
DEFAULT_LIMIT_PER_SOURCE = 8
DEFAULT_MAX_TOTAL_RESULTS = 30

_DEDUPE_TITLE_SIMILARITY_THRESHOLD = 0.6

_WORD_RE = re.compile(r"[a-z0-9]+")


def _keywords(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


# ---------------------------------------------------------------------------
# Source selection — a cheap keyword heuristic, not an LLM call. Keeping
# selection deterministic and LLM-free means it costs nothing, never blocks
# on the harness's choke point, and is trivially unit-testable.
# ---------------------------------------------------------------------------

_SELECTION_RULES: list[tuple[set[str], list[str]]] = [
    (
        {"open-source", "open", "source", "framework", "frameworks", "library", "libraries", "repo", "repository", "sdk", "agent", "agents", "tool", "tools"},
        ["github", "hackernews", "reddit", "web"],
    ),
    (
        {"product", "products", "saas", "launch", "launched", "startup", "startups"},
        ["producthunt", "hackernews", "web", "reddit"],
    ),
    (
        {"reaction", "reactions", "developer", "developers", "opinion", "opinions", "discussion", "community", "sentiment"},
        ["reddit", "hackernews", "github", "web"],
    ),
    (
        {"announcement", "announcements", "announced", "official", "coverage", "news", "release", "released"},
        ["rss", "web", "hackernews"],
    ),
]

DEFAULT_SELECTION = ["hackernews", "web", "github", "reddit"]


def select_sources(query: str, explicit: list[str] | None = None) -> list[str]:
    """Explicit sources always win outright (caller knows best). Otherwise a

    keyword match against _SELECTION_RULES picks a small, purposeful subset
    — never "call every source for every query" (PRP Source Selection).
    "x" is never chosen by the heuristic (see research_sources.py docstring)
    — it can only be reached via `explicit`.
    """
    if explicit:
        unknown = [s for s in explicit if s not in ALL_SOURCES]
        if unknown:
            raise ValueError(f"Unknown research source(s): {unknown}. Known sources: {sorted(ALL_SOURCES)}")
        return list(dict.fromkeys(explicit))

    query_kw = _keywords(query)
    best_sources: list[str] | None = None
    best_overlap = 0
    for trigger_kw, sources in _SELECTION_RULES:
        overlap = len(query_kw & trigger_kw)
        if overlap > best_overlap:
            best_overlap = overlap
            best_sources = sources
    return list(best_sources) if best_sources is not None else list(DEFAULT_SELECTION)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url)
    netloc = parsed.netloc.lower()
    netloc = netloc.removeprefix("www.")
    return f"{netloc}{parsed.path.rstrip('/')}"


def _title_similarity(a: str, b: str) -> float:
    kw_a, kw_b = _keywords(a), _keywords(b)
    if not kw_a or not kw_b:
        return 0.0
    return len(kw_a & kw_b) / len(kw_a | kw_b)


def dedupe_results(results: list[ResearchResult]) -> list[ResearchResult]:
    """Same story on multiple sources collapses into one entry, with

    `metadata["also_seen_on"]` recording which sources confirmed it and
    engagement numbers summed across sources — both inputs ranking's
    cross-source-confirmation signal reads (PRP Ranking factor 6).
    """
    kept: list[ResearchResult] = []
    kept_norm_urls: list[str] = []

    for result in results:
        norm_url = _normalize_url(result.url)
        duplicate_index: int | None = None
        for i, existing in enumerate(kept):
            same_url = bool(norm_url) and norm_url == kept_norm_urls[i]
            similar_title = _title_similarity(result.title, existing.title) >= _DEDUPE_TITLE_SIMILARITY_THRESHOLD
            if same_url or similar_title:
                duplicate_index = i
                break

        if duplicate_index is None:
            result.metadata = {**result.metadata, "also_seen_on": [result.source]}
            kept.append(result)
            kept_norm_urls.append(norm_url)
            continue

        existing = kept[duplicate_index]
        also_seen_on = list(dict.fromkeys([*existing.metadata.get("also_seen_on", [existing.source]), result.source]))
        existing.metadata = {**existing.metadata, "also_seen_on": also_seen_on}
        merged_engagement = dict(existing.engagement)
        for key, value in result.engagement.items():
            merged_engagement[key] = merged_engagement.get(key, 0) + value
        existing.engagement = merged_engagement

    return kept


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

# Static, coarse trust weights — RSS/GitHub lead because they're
# official/primary-source content; web is last because provider quality is
# unknown/aggregated. Not meant to be precise, just directionally sane.
_SOURCE_QUALITY_WEIGHT: dict[str, float] = {
    "rss": 1.0,
    "github": 0.85,
    "hackernews": 0.8,
    "producthunt": 0.7,
    "reddit": 0.65,
    "web": 0.6,
    "x": 0.5,
}

_RECENCY_HALF_LIFE_DAYS = 7.0


def _recency_score(published_at: datetime | None, now: datetime) -> float:
    if published_at is None:
        return 0.5  # unknown recency is treated as neutral, not stale
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age_days = max((now - published_at).total_seconds() / 86400, 0.0)
    return 0.5 ** (age_days / _RECENCY_HALF_LIFE_DAYS)


def _engagement_score(engagement: dict[str, int | float]) -> float:
    if not engagement:
        return 0.0
    primary = max((float(v) for v in engagement.values()), default=0.0)
    return min(math.log10(primary + 1) / 5, 1.0)


def _relevance_score(result: ResearchResult, query_kw: set[str]) -> float:
    if not query_kw:
        return 0.0
    text_kw = _keywords(f"{result.title} {result.content}")
    return len(query_kw & text_kw) / len(query_kw)


def rank_results(results: list[ResearchResult], query: str) -> list[ResearchResult]:
    """Weighted blend of relevance/recency/source-quality/engagement/novelty,

    deliberately weighted so relevance+recency+source-quality (0.75 of the
    score) dominate raw engagement (0.10) — PRP Ranking: "Do NOT rank solely
    by popularity." Cross-source confirmation (results dedupe merged from
    multiple sources) adds a flat bonus on top.
    """
    now = datetime.now(timezone.utc)
    query_kw = _keywords(query)

    for result in results:
        relevance = _relevance_score(result, query_kw)
        recency = _recency_score(result.published_at, now)
        source_quality = _SOURCE_QUALITY_WEIGHT.get(result.source, 0.5)
        engagement = _engagement_score(result.engagement)
        cross_source_bonus = 0.15 if len(result.metadata.get("also_seen_on", [])) > 1 else 0.0

        score = (0.35 * relevance) + (0.25 * recency) + (0.15 * source_quality) + (0.10 * engagement) + cross_source_bonus
        result.relevance_score = round(min(score, 1.0), 4)

    return sorted(results, key=lambda r: r.relevance_score, reverse=True)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _synthesis_run_config() -> AgentRunConfig:
    """Resolved fresh (not module-cached), matching every other runtime

    agent's build_run_config() in this codebase.
    """
    return AgentRunConfig(
        agent_name="research",
        allowed_tools=[],
        model_tier=route("research", "synthesize").tier.value,
        escalation_condition=None,
        task_type="synthesize",
    )


def _parse_synthesis(response_text: str) -> dict[str, Any]:
    if not response_text:
        return {}
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"research synthesis LLM response was not valid JSON: {response_text!r}"
        ) from exc
    if not isinstance(payload, dict):
        # ValueError (not TypeError) to match every other "malformed LLM JSON
        # output" raise in this codebase (research.py's _parse_kept_indices,
        # content_strategist.py's _parse_brief, analytics.py's digest parser).
        raise ValueError(f"research synthesis LLM response must be a JSON object: {response_text!r}")  # noqa: TRY004
    return payload


_SOURCE_LABELS: dict[str, str] = {
    "hackernews": "Hacker News",
    "reddit": "Reddit",
    "github": "GitHub",
    "producthunt": "Product Hunt",
    "rss": "RSS feeds",
    "web": "the web",
    "x": "X (Twitter)",
}


async def _fetch_source(source: str, query: str, limit: int) -> list[ResearchResult]:
    adapter = ALL_SOURCES[source]
    label = _SOURCE_LABELS.get(source, source)
    try:
        # Sources run concurrently (asyncio.gather in research()/
        # conduct_research()) against activity.py's single global slot, so
        # under concurrency the board shows whichever source most recently
        # checked in rather than every source at once — an honest
        # "last known active step" signal, not a precise per-thread tracker.
        with activity("research", "researching", detail=f"Searching {label} for {query!r}", source=source):
            return await adapter(query, limit)
    except Exception as exc:  # noqa: BLE001 — one source failing must never sink the whole run
        logger.warning("research_source_adapter_failed", source=source, error=str(exc))
        return []


# Fixed default (not the heuristic select_sources()) — the caller asked for
# these five specific sources every time unless they override `sources`.
UNIFIED_DEFAULT_SOURCES = ["hackernews", "github", "producthunt", "rss", "reddit"]


def _to_normalized_dict(result: ResearchResult) -> dict[str, Any]:
    engagement = result.engagement or {}
    return {
        "source": result.source,
        "title": result.title,
        "summary": result.content,
        "url": result.url,
        "author": result.author or "",
        "published_at": result.published_at.isoformat() if result.published_at else "",
        "score": max(engagement.values()) if engagement else None,
        "metadata": result.metadata,
    }


async def research(
    query: str,
    sources: list[str] | None = None,
    limit_per_source: int = 10,
) -> list[dict[str, Any]]:
    """Unified, synthesis-free entrypoint: fetch the given sources concurrently

    (each isolated via _fetch_source — one failing source never breaks the
    others), dedupe, rank, and return plain normalized dicts. No LLM call, no
    persistence — that's what conduct_research() is for. `sources` defaults
    to UNIFIED_DEFAULT_SOURCES rather than a mutable default argument.
    """
    selected = sources if sources is not None else list(UNIFIED_DEFAULT_SOURCES)
    unknown = [s for s in selected if s not in ALL_SOURCES]
    if unknown:
        raise ValueError(f"Unknown research source(s): {unknown}. Known sources: {sorted(ALL_SOURCES)}")

    batches = await asyncio.gather(*(_fetch_source(s, query, limit_per_source) for s in selected))
    all_results = [result for batch in batches for result in batch]

    deduped = dedupe_results(all_results)
    ranked = rank_results(deduped, query)

    return [_to_normalized_dict(r) for r in ranked]


async def conduct_research(
    query: str,
    llm_client: LLMClient,
    sources: list[str] | None = None,
    limit_per_source: int = DEFAULT_LIMIT_PER_SOURCE,
    max_total_results: int = DEFAULT_MAX_TOTAL_RESULTS,
    persist: bool = True,
    index_path: str | None = None,
) -> ResearchPackage:
    """The Researcher Agent's main entrypoint: pick sources, fetch them in

    parallel, normalize/dedupe/rank, synthesize a digest, and (by default)
    persist that digest the same way write_research_note() already does for
    single X posts.
    """
    selected = select_sources(query, explicit=sources)

    batches = await asyncio.gather(*(_fetch_source(s, query, limit_per_source) for s in selected))
    all_results = [result for batch in batches for result in batch]

    deduped = dedupe_results(all_results)
    ranked = rank_results(deduped, query)[:max_total_results]

    source_coverage = {s: 0 for s in selected}
    for result in ranked:
        source_coverage[result.source] = source_coverage.get(result.source, 0) + 1

    state = AgentState(
        task_id=str(uuid.uuid4()),
        agent_name=RuntimeAgentName.RESEARCH,
        current_task=(
            f"Given these {len(ranked)} already-deduplicated, already-ranked research results for "
            f"the query {query!r}, respond with a JSON object with exactly the keys: "
            'executive_summary (2-4 sentences), key_findings (a list of short strings), and '
            "interesting_angles (a list of short strings suggesting LinkedIn post angles). "
            "Base every claim strictly on the provided results — never invent a fact, source, or "
            "URL that isn't present in them. An empty results list is a valid input; in that case "
            "return an executive_summary saying nothing relevant was found and empty lists for the "
            "other two keys."
        ),
        scratchpad={"results": [r.model_dump(mode="json") for r in ranked]},
    )
    with activity("research", "synthesizing", detail=f"Writing up findings for {query!r}"):
        state = await run_step(state, _synthesis_run_config(), llm_client, tool_executor=None)
    response_text = state.conversation[-1]["content"] if state.conversation else ""
    synthesis = _parse_synthesis(response_text)

    citations = list(dict.fromkeys(r.url for r in ranked if r.url))

    package = ResearchPackage(
        research_query=query,
        executive_summary=str(synthesis.get("executive_summary", "")),
        key_findings=[str(f) for f in synthesis.get("key_findings", [])],
        interesting_angles=[str(a) for a in synthesis.get("interesting_angles", [])],
        source_results=ranked,
        citations=citations,
        source_coverage=source_coverage,
        research_timestamp=datetime.now(timezone.utc),
    )

    if persist and package.executive_summary and citations:
        note_id = str(uuid.uuid4())
        await execute_tool(
            "save_research_note",
            {"content": package.executive_summary, "source_url": citations[0]},
            approved=False,
        )
        await ingest_research_note(
            note_id=note_id,
            text=package.executive_summary,
            metadata={"query": query, "citations": citations, "source_coverage": source_coverage},
            index_path=index_path,
        )

    return package
