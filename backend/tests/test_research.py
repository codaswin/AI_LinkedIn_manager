from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from app.agents import research
from app.agents.research import ResearchNote, build_run_config, get_poll_interval
from app.harness.loop import ToolCallRequest
from app.llmops.prompt_registry import get_prompt, register_prompt
from app.memory.settings import set_setting
from app.tenancy import context as tenancy_context


@pytest.fixture(autouse=True)
def _ensure_prompt_registered() -> None:
    # Defensive against test ordering: prompt_registry is a global mutable
    # registry another test module's reset_for_testing() could have wiped
    # since research.py's module-level registration ran at import time.
    register_prompt("research", research.SYSTEM_PROMPT)


@pytest.fixture(autouse=True)
def _tenancy_context() -> None:
    # get_poll_interval/set_setting go through the now per-user
    # agent_settings table (see plans/peaceful-scribbling-tiger.md Stage 3).
    token = tenancy_context.set_current_user_id("research-test-user")
    yield
    tenancy_context.reset_current_user_id(token)


@dataclass
class FakeLLMResponse:
    text: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    cost_usd: float = 0.0
    confidence: float | None = None
    goal_achieved: bool = True


def _make_llm_client(text: str):
    async def _client(*, state: Any, config: Any) -> FakeLLMResponse:
        return FakeLLMResponse(text=text)

    return _client


# ---------------------------------------------------------------------------
# Hard project rule: X is read-only. No write/post/reply/DM-capable tool may
# ever appear in this agent's allowed_tools.
# ---------------------------------------------------------------------------

WRITE_CAPABLE_TOOL_NAMES = {
    "publish_post",
    "schedule_post",
    "delete_post",
    "reply_to_comment",
    "reply_to_dm",
    "send_connection_request",
    "like_post",
    "draft_post",
    "get_linkedin_notifications",
    "generate_analytics_report",
}


def test_allowed_tools_includes_all_read_only_sources_plus_internal_write_tools() -> None:
    config = build_run_config()
    assert config.allowed_tools == [
        "search_hackernews",
        "search_reddit",
        "search_web",
        "search_github",
        "search_producthunt",
        "search_rss",
        "search_x_posts",
        "save_research_note",
        "search_knowledge_base",
    ]


def test_allowed_tools_contains_no_write_post_reply_dm_capable_tool() -> None:
    config = build_run_config()
    offenders = set(config.allowed_tools) & WRITE_CAPABLE_TOOL_NAMES
    assert offenders == set(), f"research agent must never be allowed a write-capable tool: {offenders}"


def test_run_config_never_escalates() -> None:
    config = build_run_config()
    assert config.escalation_condition is None


def test_run_config_agent_name_and_task_type() -> None:
    config = build_run_config()
    assert config.agent_name == "research"
    assert config.task_type == "digest"


# ---------------------------------------------------------------------------
# System prompt registration + model tier
# ---------------------------------------------------------------------------

EXPECTED_SYSTEM_PROMPT = (
    "You are the Research Agent for a professional's LinkedIn presence. You "
    "track developments in AI, Agentic AI, Hermes, and AI tooling across "
    "multiple sources — Hacker News, Reddit, the general web, GitHub, "
    "Product Hunt, and RSS feeds by default, plus X (Twitter) only when "
    "explicitly requested — and turn what you find into short research "
    "notes the Content Strategist can use when picking post topics. Your "
    "access to every source, including X, is search/read only — you can "
    "never post, reply, like, or message on any platform, under any "
    "circumstance, regardless of how relevant or time-sensitive something "
    "seems. Triage high volumes of results quickly and cheaply; only the "
    "small number worth keeping get written up properly, and every claim "
    "you make must be grounded in a source you actually retrieved."
)


def test_system_prompt_is_registered_and_matches_exact_text() -> None:
    assert research.SYSTEM_PROMPT == EXPECTED_SYSTEM_PROMPT
    assert get_prompt("research") == EXPECTED_SYSTEM_PROMPT


def test_config_resolves_to_primary_tier_for_digest_step() -> None:
    config = build_run_config()
    assert config.model_tier == "primary"


# ---------------------------------------------------------------------------
# get_poll_interval — reads the DB-backed setting live, never hardcoded
# ---------------------------------------------------------------------------


async def test_get_poll_interval_defaults_to_daily(db_session) -> None:
    value = await get_poll_interval(db_session)
    assert value == "daily"


async def test_get_poll_interval_reflects_updated_setting(db_session) -> None:
    before = await get_poll_interval(db_session)
    assert before == "daily"

    await set_setting(db_session, "research_agent.poll_interval", "hourly", updated_by="dashboard_ui:test")

    after = await get_poll_interval(db_session)
    assert after == "hourly"


def test_poll_interval_to_timedelta_known_values() -> None:
    from datetime import timedelta

    assert research.poll_interval_to_timedelta("daily") == timedelta(days=1)
    assert research.poll_interval_to_timedelta("hourly") == timedelta(hours=1)


def test_poll_interval_to_timedelta_unknown_value_raises() -> None:
    with pytest.raises(ValueError, match="Unrecognized"):
        research.poll_interval_to_timedelta("fortnightly")


# ---------------------------------------------------------------------------
# write_research_note — dual write to save_research_note AND ingest_research_note
# ---------------------------------------------------------------------------


async def test_write_research_note_calls_both_save_and_ingest_with_consistent_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_tool_calls: list[tuple[str, dict[str, Any], bool]] = []
    ingest_calls: list[dict[str, Any]] = []

    async def fake_execute_tool(tool_name: str, raw_arguments: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        execute_tool_calls.append((tool_name, raw_arguments, approved))
        return {
            "tool_name": tool_name,
            "status": "success",
            "result": {"note_id": "internal-tool-id", "status": "saved"},
            "error": None,
            "latency_seconds": 0.01,
        }

    async def fake_ingest_research_note(
        note_id: str, text: str, metadata: dict[str, Any] | None = None, *, index_path: str | None = None
    ) -> int:
        ingest_calls.append({"note_id": note_id, "text": text, "metadata": metadata, "index_path": index_path})
        return 1

    monkeypatch.setattr(research, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(research, "ingest_research_note", fake_ingest_research_note)

    llm_payload = {
        "note_id": "note-abc",
        "summary": "New agentic tool-calling benchmark released for Hermes-class models.",
        "source_url": "https://x.com/someuser/status/12345",
        "topic_tags": ["agentic-ai", "hermes", "benchmarks"],
    }
    llm_client = _make_llm_client(json.dumps(llm_payload))

    note = await research.write_research_note({"text": "raw x post content"}, llm_client=llm_client)

    assert note == ResearchNote(**llm_payload)

    assert len(execute_tool_calls) == 1
    tool_name, tool_args, approved = execute_tool_calls[0]
    assert tool_name == "save_research_note"
    assert approved is False
    assert tool_args == {"content": note.summary, "source_url": note.source_url}

    assert len(ingest_calls) == 1
    assert ingest_calls[0]["note_id"] == note.note_id
    assert ingest_calls[0]["text"] == note.summary
    assert ingest_calls[0]["metadata"]["source_url"] == note.source_url
    assert ingest_calls[0]["metadata"]["topic_tags"] == note.topic_tags


async def test_write_research_note_raises_on_non_json_llm_response(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute_tool(tool_name: str, raw_arguments: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        raise AssertionError("save_research_note must not be called when the LLM output can't be parsed")

    async def fake_ingest_research_note(*args: Any, **kwargs: Any) -> int:
        raise AssertionError("ingest_research_note must not be called when the LLM output can't be parsed")

    monkeypatch.setattr(research, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(research, "ingest_research_note", fake_ingest_research_note)

    llm_client = _make_llm_client("not json")

    with pytest.raises(ValueError):
        await research.write_research_note({"text": "raw x post"}, llm_client=llm_client)


# ---------------------------------------------------------------------------
# triage_x_posts — cheap worker-tier filter over search_x_posts results
# ---------------------------------------------------------------------------


async def test_triage_x_posts_filters_via_search_x_posts_and_worker_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_configs: list[Any] = []

    async def fake_execute_tool(tool_name: str, raw_arguments: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        assert tool_name == "search_x_posts"
        assert raw_arguments == {"query": "agentic AI", "max_results": 3}
        assert approved is False
        return {
            "tool_name": tool_name,
            "status": "success",
            "result": {
                "posts": [
                    {"id": "1", "text": "substantive agentic AI update"},
                    {"id": "2", "text": "spam / ad"},
                    {"id": "3", "text": "another substantive update"},
                ]
            },
            "error": None,
            "latency_seconds": 0.01,
        }

    monkeypatch.setattr(research, "execute_tool", fake_execute_tool)

    async def fake_llm_client(*, state: Any, config: Any) -> FakeLLMResponse:
        captured_configs.append(config)
        return FakeLLMResponse(text=json.dumps([0, 2]))

    kept = await research.triage_x_posts("agentic AI", llm_client=fake_llm_client, top_k=3)

    assert [p["id"] for p in kept] == ["1", "3"]
    assert len(captured_configs) == 1
    assert captured_configs[0].model_tier == "worker"
    assert captured_configs[0].agent_name == "research"


async def test_triage_x_posts_returns_empty_when_search_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute_tool(tool_name: str, raw_arguments: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        return {"tool_name": tool_name, "status": "success", "result": {"posts": []}, "error": None, "latency_seconds": 0.0}

    monkeypatch.setattr(research, "execute_tool", fake_execute_tool)

    async def fake_llm_client(*, state: Any, config: Any) -> FakeLLMResponse:
        raise AssertionError("LLM should not be called when there is nothing to triage")

    kept = await research.triage_x_posts("agentic AI", llm_client=fake_llm_client, top_k=3)
    assert kept == []
