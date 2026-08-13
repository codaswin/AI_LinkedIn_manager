from __future__ import annotations

import inspect
import json
import typing
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.agents import analytics
from app.agents.analytics import (
    ANALYTICS_AGENT_CONFIG,
    ANALYTICS_SYSTEM_PROMPT,
    WeeklyDigest,
    generate_weekly_digest,
    suggest_deletion,
)
from app.harness.loop import AgentRunConfig, ToolCallRequest
from app.harness.state import AgentState
from app.llmops.model_router import ModelTier
from app.llmops.prompt_registry import get_prompt
from app.tools import registry as registry_module

registry_module._import_all_tools()


@dataclass
class FakeLLMResponse:
    text: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    cost_usd: float = 0.0
    confidence: float | None = None
    goal_achieved: bool = True


def _fake_llm_client(flagged_posts: list[dict[str, Any]]):
    async def _client(*, state: AgentState, config: AgentRunConfig) -> FakeLLMResponse:
        return FakeLLMResponse(text=json.dumps({"flagged_posts": flagged_posts}))

    return _client


def _valid_delete_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "post_id": "post-123",
        "post_content": "Original post text about a product launch.",
        "published_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "engagement_stats": {"likes": 3, "comments": 0, "impressions": 40},
        "reason": "Zero engagement after 60 days and a broken outbound link.",
    }
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# generate_weekly_digest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_weekly_digest_calls_report_tool_without_approval() -> None:
    captured: dict[str, Any] = {}
    real_execute_tool = analytics.execute_tool

    async def spy_execute_tool(tool_name: str, raw_arguments: dict[str, Any], approved: bool = False):
        captured["tool_name"] = tool_name
        captured["approved"] = approved
        return await real_execute_tool(tool_name, raw_arguments, approved=approved)

    analytics.execute_tool = spy_execute_tool
    try:
        await generate_weekly_digest(
            db=None,
            llm_client=_fake_llm_client([]),
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 8),
        )
    finally:
        analytics.execute_tool = real_execute_tool

    assert captured["tool_name"] == "generate_analytics_report"
    assert captured["approved"] is False


@pytest.mark.asyncio
async def test_generate_weekly_digest_routes_llm_call_through_run_step() -> None:
    """CLAUDE.md non-negotiable #1: no module may call an LLM client directly — every call must

    go through harness.loop.run_step(). This asserts the digest's judgment call receives a real
    AgentState/AgentRunConfig (run_step's signature), not a bespoke keyword-argument shape.
    """
    captured: dict[str, Any] = {}

    async def spying_llm_client(*, state: AgentState, config: AgentRunConfig) -> FakeLLMResponse:
        captured["state"] = state
        captured["config"] = config
        return FakeLLMResponse(
            text=json.dumps({"flagged_posts": [{"post_id": "post-9", "reason": "No engagement in 90 days"}]})
        )

    digest = await generate_weekly_digest(
        db=None,
        llm_client=spying_llm_client,
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 8),
    )

    assert isinstance(captured["state"], AgentState)
    assert captured["config"] is ANALYTICS_AGENT_CONFIG
    assert digest.flagged_posts == [{"post_id": "post-9", "reason": "No engagement in 90 days"}]


@pytest.mark.asyncio
async def test_generate_weekly_digest_returns_well_formed_digest() -> None:
    digest = await generate_weekly_digest(
        db=None,
        llm_client=_fake_llm_client([{"post_id": "post-9", "reason": "No engagement in 90 days"}]),
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 8),
    )

    assert isinstance(digest, WeeklyDigest)
    assert digest.period_start == date(2026, 8, 1)
    assert digest.period_end == date(2026, 8, 8)
    assert digest.total_impressions == 0
    assert digest.avg_engagement_rate == 0.0
    assert digest.follower_delta == 0
    assert digest.flagged_posts == [{"post_id": "post-9", "reason": "No engagement in 90 days"}]


@pytest.mark.asyncio
async def test_generate_weekly_digest_defaults_flagged_posts_to_empty_list() -> None:
    async def fake_llm_client(*, state: AgentState, config: AgentRunConfig) -> FakeLLMResponse:
        return FakeLLMResponse(text="")

    digest = await generate_weekly_digest(
        db=None,
        llm_client=fake_llm_client,
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 8),
    )

    assert digest.flagged_posts == []


# ---------------------------------------------------------------------------
# suggest_deletion — always submits, never confidence-gated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggest_deletion_always_submits_regardless_of_confidence_wording() -> None:
    """No confidence parameter exists on suggest_deletion at all — this test would fail if

    anyone ever bolted a confidence-based skip onto this function later, because it calls it with
    a reason that reads like "this is obviously fine, don't bother a human" and still asserts the
    approval queue was hit unconditionally.
    """
    submit_approval_fn = AsyncMock(return_value={"status": "pending", "id": "appr-1"})

    result = await suggest_deletion(
        **_valid_delete_kwargs(
            reason="Low-confidence guess, probably nothing, but flagging just in case."
        ),
        submit_approval_fn=submit_approval_fn,
    )

    submit_approval_fn.assert_awaited_once()
    _, kwargs = submit_approval_fn.call_args
    assert kwargs["tool_name"] == "delete_post"
    assert kwargs["requested_by_agent"] == "analytics"
    arguments = kwargs["arguments"]
    assert arguments["post_id"] == "post-123"
    assert arguments["post_content"] == "Original post text about a product launch."
    assert arguments["published_at"] == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert arguments["engagement_stats"] == {"likes": 3, "comments": 0, "impressions": 40}
    assert result == {"status": "pending", "id": "appr-1"}


@pytest.mark.asyncio
async def test_suggest_deletion_signature_has_no_confidence_parameter() -> None:
    params = inspect.signature(suggest_deletion).parameters
    assert "confidence" not in params


@pytest.mark.asyncio
async def test_suggest_deletion_raises_on_missing_post_content() -> None:
    submit_approval_fn = AsyncMock()
    kwargs = _valid_delete_kwargs(post_content="")

    with pytest.raises(ValueError, match="post_content"):
        await suggest_deletion(**kwargs, submit_approval_fn=submit_approval_fn)

    submit_approval_fn.assert_not_awaited()


@pytest.mark.asyncio
async def test_suggest_deletion_raises_on_missing_published_at() -> None:
    submit_approval_fn = AsyncMock()
    kwargs = _valid_delete_kwargs(published_at=None)

    with pytest.raises(ValueError, match="published_at"):
        await suggest_deletion(**kwargs, submit_approval_fn=submit_approval_fn)

    submit_approval_fn.assert_not_awaited()


@pytest.mark.asyncio
async def test_suggest_deletion_raises_on_missing_engagement_stats() -> None:
    submit_approval_fn = AsyncMock()
    kwargs = _valid_delete_kwargs(engagement_stats={})

    with pytest.raises(ValueError, match="engagement_stats"):
        await suggest_deletion(**kwargs, submit_approval_fn=submit_approval_fn)

    submit_approval_fn.assert_not_awaited()


@pytest.mark.asyncio
async def test_suggest_deletion_raises_on_missing_reason() -> None:
    submit_approval_fn = AsyncMock()
    kwargs = _valid_delete_kwargs(reason="")

    with pytest.raises(ValueError, match="reason"):
        await suggest_deletion(**kwargs, submit_approval_fn=submit_approval_fn)

    submit_approval_fn.assert_not_awaited()


def test_suggest_deletion_has_no_batch_or_list_parameter() -> None:
    params = inspect.signature(suggest_deletion).parameters
    assert "post_ids" not in params
    assert "posts" not in params
    for name in params:
        assert "batch" not in name.lower()
    hints = typing.get_type_hints(suggest_deletion)
    assert hints["post_id"] is str


# ---------------------------------------------------------------------------
# Prompt registration + model tier
# ---------------------------------------------------------------------------


def test_prompt_registered_with_exact_text() -> None:
    assert get_prompt("analytics") == ANALYTICS_SYSTEM_PROMPT
    assert "never" in ANALYTICS_SYSTEM_PROMPT.lower()


def test_config_resolves_to_cheap_tier() -> None:
    assert ANALYTICS_AGENT_CONFIG.model_tier == ModelTier.CHEAP.value
    assert ANALYTICS_AGENT_CONFIG.model_tier == "cheap"


def test_config_agent_name_and_allowed_tools() -> None:
    assert ANALYTICS_AGENT_CONFIG.agent_name == "analytics"
    assert ANALYTICS_AGENT_CONFIG.allowed_tools == [
        "generate_analytics_report",
        "search_knowledge_base",
        "delete_post",
    ]


def test_config_escalation_condition_is_none() -> None:
    assert ANALYTICS_AGENT_CONFIG.escalation_condition is None
