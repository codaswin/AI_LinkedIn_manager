from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from app.agents import research_pipeline
from app.agents.research_schema import ResearchPackage, ResearchResult
from app.harness.loop import ToolCallRequest
from app.harness.state import AgentState
from app.tenancy import context as tenancy_context


@pytest.fixture(autouse=True)
def _tenancy_context():
    token = tenancy_context.set_current_user_id("user-research-pipeline-test")
    yield
    tenancy_context.reset_current_user_id(token)

NOW = datetime.now(timezone.utc)


@dataclass
class FakeLLMResponse:
    text: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    cost_usd: float = 0.0
    confidence: float | None = None
    goal_achieved: bool = True


def _synthesis_llm_client(payload: dict[str, Any]):
    async def _client(*, state: AgentState, config: Any) -> FakeLLMResponse:
        return FakeLLMResponse(text=json.dumps(payload))

    return _client


# ---------------------------------------------------------------------------
# select_sources
# ---------------------------------------------------------------------------


def test_explicit_sources_are_used_verbatim_deduped() -> None:
    assert research_pipeline.select_sources("anything", explicit=["github", "web", "github"]) == ["github", "web"]


def test_explicit_unknown_source_raises() -> None:
    with pytest.raises(ValueError, match="Unknown research source"):
        research_pipeline.select_sources("anything", explicit=["not-a-real-source"])


def test_explicit_x_is_allowed_when_requested() -> None:
    assert research_pipeline.select_sources("anything", explicit=["x"]) == ["x"]


def test_heuristic_never_selects_x() -> None:
    for query in [
        "Find newly released open-source AI agent frameworks",
        "Find interesting AI SaaS products launched recently",
        "Research developer reaction to a new framework",
        "Find official announcements and industry coverage",
        "some totally generic query",
    ]:
        assert "x" not in research_pipeline.select_sources(query)


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Find newly released open-source AI agent frameworks", ["github", "hackernews", "reddit", "web"]),
        ("Find interesting AI SaaS products launched recently", ["producthunt", "hackernews", "web", "reddit"]),
        ("Research developer reaction to a new framework", ["reddit", "hackernews", "github", "web"]),
        ("Find official announcements and industry coverage", ["rss", "web", "hackernews"]),
    ],
)
def test_heuristic_matches_prp_examples(query: str, expected: list[str]) -> None:
    assert research_pipeline.select_sources(query) == expected


def test_unmatched_query_falls_back_to_default_selection() -> None:
    assert research_pipeline.select_sources("xyzzy plugh") == research_pipeline.DEFAULT_SELECTION


# ---------------------------------------------------------------------------
# dedupe_results
# ---------------------------------------------------------------------------


def _result(source: str, title: str, url: str, **kwargs: Any) -> ResearchResult:
    return ResearchResult(source=source, title=title, url=url, **kwargs)


def test_dedupe_merges_by_normalized_url() -> None:
    a = _result("hackernews", "Agentic AI news", "https://example.com/post?utm_source=hn", engagement={"score": 10})
    b = _result("reddit", "Agentic AI news (repost)", "https://www.example.com/post/", engagement={"score": 5})

    deduped = research_pipeline.dedupe_results([a, b])

    assert len(deduped) == 1
    assert set(deduped[0].metadata["also_seen_on"]) == {"hackernews", "reddit"}
    assert deduped[0].engagement == {"score": 15}


def test_dedupe_merges_by_similar_title_when_urls_differ() -> None:
    a = _result("hackernews", "New agentic AI framework launches today", "https://a.example.com/x")
    b = _result("producthunt", "New agentic AI framework launches today", "https://b.example.com/y")

    deduped = research_pipeline.dedupe_results([a, b])
    assert len(deduped) == 1


def test_dedupe_keeps_distinct_stories_separate() -> None:
    a = _result("hackernews", "Agentic AI framework released", "https://a.example.com/x")
    b = _result("hackernews", "Completely unrelated cooking recipe", "https://b.example.com/y")

    deduped = research_pipeline.dedupe_results([a, b])
    assert len(deduped) == 2


# ---------------------------------------------------------------------------
# rank_results
# ---------------------------------------------------------------------------


def test_rank_orders_by_relevance_recency_and_source_quality() -> None:
    relevant_recent = _result(
        "rss", "agentic AI framework release", "https://a.example.com", published_at=NOW - timedelta(hours=1)
    )
    irrelevant_old = _result(
        "web", "sourdough bread recipe", "https://b.example.com", published_at=NOW - timedelta(days=365)
    )

    ranked = research_pipeline.rank_results([irrelevant_old, relevant_recent], "agentic AI framework")
    assert ranked[0].url == "https://a.example.com"
    assert ranked[0].relevance_score > ranked[1].relevance_score


def test_rank_does_not_rank_solely_by_engagement() -> None:
    high_engagement_irrelevant = _result(
        "web", "unrelated viral meme", "https://viral.example.com", engagement={"score": 100000}
    )
    relevant_low_engagement = _result(
        "rss", "agentic AI framework announcement", "https://official.example.com", engagement={"score": 1}, published_at=NOW
    )

    ranked = research_pipeline.rank_results(
        [high_engagement_irrelevant, relevant_low_engagement], "agentic AI framework announcement"
    )
    assert ranked[0].url == "https://official.example.com"


def test_rank_gives_cross_source_confirmation_bonus() -> None:
    single_source = _result("web", "some topic", "https://a.example.com", metadata={"also_seen_on": ["web"]})
    confirmed = _result(
        "web", "some topic", "https://b.example.com", metadata={"also_seen_on": ["web", "hackernews", "reddit"]}
    )

    ranked = research_pipeline.rank_results([single_source, confirmed], "some topic")
    assert ranked[0].url == "https://b.example.com"


def test_rank_handles_missing_published_at_neutrally() -> None:
    result = _result("web", "agentic AI", "https://a.example.com", published_at=None)
    ranked = research_pipeline.rank_results([result], "agentic AI")
    assert 0.0 <= ranked[0].relevance_score <= 1.0


# ---------------------------------------------------------------------------
# conduct_research — full orchestration
# ---------------------------------------------------------------------------


async def test_conduct_research_runs_selected_sources_in_parallel_and_synthesizes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_github(query: str, limit: int) -> list[ResearchResult]:
        return [_result("github", "agentic-framework", "https://github.com/x/agentic-framework")]

    async def fake_hackernews(query: str, limit: int) -> list[ResearchResult]:
        return [_result("hackernews", "Agentic AI framework hits HN", "https://news.ycombinator.com/item?id=1")]

    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "github", fake_github)
    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "hackernews", fake_hackernews)

    llm_client = _synthesis_llm_client(
        {
            "executive_summary": "A new agentic AI framework is gaining traction.",
            "key_findings": ["Released on GitHub", "Discussed on Hacker News"],
            "interesting_angles": ["Why open-source agent frameworks are heating up"],
        }
    )

    package = await research_pipeline.conduct_research(
        "open-source AI agent frameworks", llm_client=llm_client, sources=["github", "hackernews"], persist=False
    )

    assert isinstance(package, ResearchPackage)
    assert package.research_query == "open-source AI agent frameworks"
    assert package.executive_summary == "A new agentic AI framework is gaining traction."
    assert set(package.source_coverage) == {"github", "hackernews"}
    assert len(package.source_results) == 2
    assert set(package.citations) == {
        "https://github.com/x/agentic-framework",
        "https://news.ycombinator.com/item?id=1",
    }


async def test_conduct_research_isolates_a_failing_source(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_github(query: str, limit: int) -> list[ResearchResult]:
        raise RuntimeError("github is down")

    async def fake_web(query: str, limit: int) -> list[ResearchResult]:
        return [_result("web", "agentic AI framework news", "https://example.com/news")]

    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "github", fake_github)
    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "web", fake_web)

    llm_client = _synthesis_llm_client({"executive_summary": "Some news.", "key_findings": [], "interesting_angles": []})

    package = await research_pipeline.conduct_research(
        "agentic AI framework", llm_client=llm_client, sources=["github", "web"], persist=False
    )

    assert package.source_coverage["github"] == 0
    assert package.source_coverage["web"] == 1
    assert len(package.source_results) == 1


async def test_conduct_research_handles_all_sources_returning_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_empty(query: str, limit: int) -> list[ResearchResult]:
        return []

    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "github", fake_empty)

    llm_client = _synthesis_llm_client(
        {"executive_summary": "Nothing relevant was found.", "key_findings": [], "interesting_angles": []}
    )

    package = await research_pipeline.conduct_research(
        "agentic AI framework", llm_client=llm_client, sources=["github"], persist=False
    )
    assert package.source_results == []
    assert package.citations == []


async def test_conduct_research_never_fabricates_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_github(query: str, limit: int) -> list[ResearchResult]:
        return [_result("github", "real repo", "https://github.com/real/repo")]

    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "github", fake_github)

    # LLM tries to sneak in a citations field — must be ignored; the schema
    # has no field for the LLM to populate citations into at all.
    llm_client = _synthesis_llm_client(
        {
            "executive_summary": "Summary.",
            "key_findings": [],
            "interesting_angles": [],
            "citations": ["https://fabricated.example.com/not-real"],
        }
    )

    package = await research_pipeline.conduct_research(
        "agentic AI", llm_client=llm_client, sources=["github"], persist=False
    )
    assert package.citations == ["https://github.com/real/repo"]
    assert "https://fabricated.example.com/not-real" not in package.citations


async def test_conduct_research_persists_via_save_and_ingest_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_github(query: str, limit: int) -> list[ResearchResult]:
        return [_result("github", "real repo", "https://github.com/real/repo")]

    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "github", fake_github)

    save_calls: list[tuple[str, dict[str, Any]]] = []
    ingest_calls: list[dict[str, Any]] = []

    async def fake_execute_tool(tool_name: str, raw_arguments: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        save_calls.append((tool_name, raw_arguments))
        return {"status": "success", "result": {"note_id": "x", "status": "saved"}}

    async def fake_ingest_research_note(note_id, text, metadata=None, *, index_path=None):
        ingest_calls.append({"note_id": note_id, "text": text, "metadata": metadata})
        return 1

    monkeypatch.setattr(research_pipeline, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(research_pipeline, "ingest_research_note", fake_ingest_research_note)

    llm_client = _synthesis_llm_client({"executive_summary": "Summary.", "key_findings": [], "interesting_angles": []})

    await research_pipeline.conduct_research("agentic AI", llm_client=llm_client, sources=["github"], persist=True)

    assert len(save_calls) == 1
    assert save_calls[0][0] == "save_research_note"
    assert len(ingest_calls) == 1
    assert ingest_calls[0]["text"] == "Summary."


async def test_conduct_research_skips_persistence_when_no_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_empty(query: str, limit: int) -> list[ResearchResult]:
        return []

    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "github", fake_empty)

    async def fake_execute_tool(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("save_research_note must not be called when there are no citations")

    monkeypatch.setattr(research_pipeline, "execute_tool", fake_execute_tool)

    llm_client = _synthesis_llm_client({"executive_summary": "Nothing found.", "key_findings": [], "interesting_angles": []})

    await research_pipeline.conduct_research("agentic AI", llm_client=llm_client, sources=["github"], persist=True)


async def test_conduct_research_respects_max_total_results_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_many(query: str, limit: int) -> list[ResearchResult]:
        return [_result("github", f"repo {i}", f"https://github.com/x/repo-{i}") for i in range(limit)]

    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "github", fake_many)

    llm_client = _synthesis_llm_client({"executive_summary": "s", "key_findings": [], "interesting_angles": []})

    package = await research_pipeline.conduct_research(
        "agentic AI",
        llm_client=llm_client,
        sources=["github"],
        limit_per_source=20,
        max_total_results=5,
        persist=False,
    )
    assert len(package.source_results) == 5


async def test_conduct_research_raises_on_non_json_synthesis_response(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_empty(query: str, limit: int) -> list[ResearchResult]:
        return []

    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "github", fake_empty)

    async def fake_llm_client(*, state: AgentState, config: Any) -> FakeLLMResponse:
        return FakeLLMResponse(text="not json")

    with pytest.raises(ValueError):
        await research_pipeline.conduct_research(
            "agentic AI", llm_client=fake_llm_client, sources=["github"], persist=False
        )
