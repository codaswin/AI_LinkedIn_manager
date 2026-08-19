from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from app.agents import content_strategist
from app.agents.content_strategist import PostBrief, build_post_brief, build_run_config
from app.harness.loop import AgentRunConfig, AgentState, ToolCallRequest
from app.llmops.prompt_registry import get_prompt, register_prompt
from app.tenancy import context as tenancy_context


@pytest.fixture(autouse=True)
def _tenancy_context():
    token = tenancy_context.set_current_user_id("user-content-strategist-test")
    yield
    tenancy_context.reset_current_user_id(token)


@pytest.fixture(autouse=True)
def _ensure_prompt_registered() -> None:
    # Defensive against test ordering: prompt_registry is a global mutable
    # registry and another test module's reset_for_testing() could have
    # wiped it since content_strategist.py's module-level registration ran
    # at import time. Re-registering here is idempotent (register_prompt
    # just appends another identical version) and keeps this file correct
    # in isolation or as part of the full suite.
    register_prompt("content_strategist", content_strategist.SYSTEM_PROMPT)


# ---------------------------------------------------------------------------
# System prompt registration
# ---------------------------------------------------------------------------

EXPECTED_SYSTEM_PROMPT = (
    "You are the Content Strategist for a professional's LinkedIn presence. "
    "Your only job is deciding WHAT to post about next — never write the post "
    "itself. Given the content calendar, recent trending-topic research "
    "(including the Research Agent's X findings), and the last 30 days of "
    "published posts, produce ONE structured brief: topic, angle, target "
    "format (text/article/poll), and target publish date. "
    "If the content calendar lists specific entries, your topic MUST be drawn "
    "from one of them (verbatim, or a close variation) — a human chose those "
    "entries deliberately, so treat them as the assignment, not as one signal "
    "among several to weigh against your own ideas. Only fall back to "
    "inventing a topic yourself when the calendar is empty; then use the "
    "retrieved research below to pick something fresh. Leave target publish "
    "date null by default — that means the post publishes as soon as a human "
    "approves it, which is what a human clicking 'approve' expects. Only set "
    "an explicit future date when the content calendar names one; never invent "
    "a delay on your own (e.g. defaulting to 'next week') just because content "
    "calendars conventionally look that way. Favor topics not already covered "
    "in the last 30 days. Ground the angle in retrieved research rather than "
    "generic commentary. Output only the brief — never post copy, that's the "
    "Content Writer's job."
)


def test_system_prompt_is_registered_and_retrievable() -> None:
    assert get_prompt("content_strategist") == EXPECTED_SYSTEM_PROMPT
    assert content_strategist.SYSTEM_PROMPT == EXPECTED_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# AgentRunConfig
# ---------------------------------------------------------------------------


def test_run_config_resolves_to_cheap_tier() -> None:
    config = build_run_config()
    assert config.model_tier == "cheap"


def test_run_config_allowed_tools_is_search_knowledge_base_only() -> None:
    config = build_run_config()
    assert config.allowed_tools == ["search_knowledge_base"]


def test_run_config_never_escalates() -> None:
    config = build_run_config()
    assert config.escalation_condition is None


def test_run_config_agent_name_matches_routing_table_key() -> None:
    config = build_run_config()
    assert config.agent_name == "content_strategist"


# ---------------------------------------------------------------------------
# build_post_brief
# ---------------------------------------------------------------------------


@dataclass
class FakeLLMResponse:
    text: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    cost_usd: float = 0.0
    confidence: float | None = None
    goal_achieved: bool = True


STALE_HIT = {
    "id": "news-1",
    "text": "Agentic AI orchestration frameworks are evolving fast this quarter",
    "source_type": "industry_news",
}
FRESH_HIT = {
    "id": "xnote-1",
    "text": "Retrieval augmented generation grounding techniques for evals",
    "source_type": "x_research_notes",
}
RECENT_POST_TOPICS = ["Agentic AI orchestration frameworks"]


async def _fake_retrieve(
    *, query: str, source_types: list[str] | None, top_k: int, index_path: str | None = None
) -> list[dict[str, Any]]:
    assert source_types == ["industry_news", "x_research_notes"]
    return [STALE_HIT, FRESH_HIT]


@pytest.mark.asyncio
async def test_build_post_brief_excludes_topics_overlapping_recent_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(content_strategist, "retrieve", _fake_retrieve)

    captured_state: AgentState | None = None
    captured_config: AgentRunConfig | None = None

    async def fake_llm_client(*, state: AgentState, config: AgentRunConfig) -> FakeLLMResponse:
        nonlocal captured_state, captured_config
        captured_state = state
        captured_config = config
        return FakeLLMResponse(
            text=json.dumps(
                {
                    "topic": "Evaluating agentic RAG pipelines",
                    "angle": "Grounding evals in retrieved research, not vibes",
                    "format": "text",
                    "target_publish_date": "2026-08-20",
                }
            )
        )

    brief = await build_post_brief(
        recent_post_topics=RECENT_POST_TOPICS,
        calendar_entries=["AI tooling week"],
        llm_client=fake_llm_client,
    )

    assert captured_state is not None
    grounding_context = captured_state.scratchpad["grounding_context"]
    grounding_ids = {hit["id"] for hit in grounding_context}
    assert FRESH_HIT["id"] in grounding_ids
    assert STALE_HIT["id"] not in grounding_ids

    assert captured_config is not None
    assert captured_config.allowed_tools == ["search_knowledge_base"]

    assert brief == PostBrief(
        topic="Evaluating agentic RAG pipelines",
        angle="Grounding evals in retrieved research, not vibes",
        format="text",
        target_publish_date=date(2026, 8, 20),
    )


@pytest.mark.asyncio
async def test_build_post_brief_falls_back_to_full_context_when_everything_overlaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def all_stale_retrieve(
        *, query: str, source_types: list[str] | None, top_k: int, index_path: str | None = None
    ) -> list[dict[str, Any]]:
        return [STALE_HIT]

    monkeypatch.setattr(content_strategist, "retrieve", all_stale_retrieve)

    captured_state: AgentState | None = None

    async def fake_llm_client(*, state: AgentState, config: AgentRunConfig) -> FakeLLMResponse:
        nonlocal captured_state
        captured_state = state
        return FakeLLMResponse(
            text=json.dumps(
                {
                    "topic": "Agentic orchestration retrospective",
                    "angle": "What changed this quarter",
                    "format": "article",
                    "target_publish_date": None,
                }
            )
        )

    brief = await build_post_brief(
        recent_post_topics=RECENT_POST_TOPICS,
        calendar_entries=["AI tooling week"],
        llm_client=fake_llm_client,
    )

    assert captured_state is not None
    grounding_context = captured_state.scratchpad["grounding_context"]
    assert [hit["id"] for hit in grounding_context] == [STALE_HIT["id"]]
    assert brief.format == "article"
    assert brief.target_publish_date is None


@pytest.mark.asyncio
async def test_build_post_brief_raises_on_non_json_llm_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(content_strategist, "retrieve", _fake_retrieve)

    async def fake_llm_client(*, state: AgentState, config: AgentRunConfig) -> FakeLLMResponse:
        return FakeLLMResponse(text="not json")

    with pytest.raises(ValueError):
        await build_post_brief(
            recent_post_topics=RECENT_POST_TOPICS,
            calendar_entries=["AI tooling week"],
            llm_client=fake_llm_client,
        )
