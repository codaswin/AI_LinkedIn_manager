from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from app.agents import content_writer
from app.harness.loop import ToolCallRequest
from app.llmops.prompt_registry import get_prompt


@dataclass
class FakeLLMResponse:
    text: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    cost_usd: float = 0.0
    confidence: float | None = None
    goal_achieved: bool = True


async def _fake_retrieve(
    *,
    query: str,
    source_types: list[str] | None = None,
    top_k: int = 5,
    index_path: str | None = None,
) -> list[dict[str, Any]]:
    return [{"source_type": "brand_voice", "text": "sample brand voice chunk", "score": 0.9}]


def _make_llm_client(confidence: float, text: str = "Drafted LinkedIn post about AI agents."):
    async def _llm_client(*, state: Any, config: Any) -> FakeLLMResponse:
        return FakeLLMResponse(text=text, confidence=confidence)

    return _llm_client


@pytest.fixture(autouse=True)
def _patch_retrieve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content_writer, "retrieve", _fake_retrieve)


def test_prompt_registered_correctly() -> None:
    assert get_prompt("content_writer") == content_writer.SYSTEM_PROMPT
    assert "Content Writer" in get_prompt("content_writer")


def test_config_resolves_to_primary_tier() -> None:
    assert content_writer.CONTENT_WRITER_CONFIG.model_tier == "primary"
    assert content_writer.CONTENT_WRITER_CONFIG.agent_name == "content_writer"


def test_allowed_tools_never_contains_gated_tools() -> None:
    allowed = content_writer.CONTENT_WRITER_CONFIG.allowed_tools
    assert "publish_post" not in allowed
    assert "schedule_post" not in allowed
    assert allowed == ["search_knowledge_base", "draft_post"]


class TestMeaningfulGrounding:
    """Regression coverage for the fabricated-case-study incident: rag.retrieve()

    always returns its top_k nearest vectors, even when none are actually
    relevant (score 0) — content_writer must not treat those as real
    grounding, verified live against an index containing only leftover
    unrelated brand_voice fixtures.
    """

    def test_zero_score_hits_are_not_meaningful_grounding(self) -> None:
        hits = [{"source_type": "brand_voice", "text": "updated", "score": 0.0}]
        assert content_writer._has_meaningful_grounding(hits) is False

    def test_positive_score_hit_is_meaningful_grounding(self) -> None:
        hits = [{"source_type": "brand_voice", "text": "real match", "score": 0.42}]
        assert content_writer._has_meaningful_grounding(hits) is True

    def test_empty_hits_are_not_meaningful_grounding(self) -> None:
        assert content_writer._has_meaningful_grounding([]) is False

    async def test_thin_grounding_is_stripped_and_task_warns_against_fabrication(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_retrieve(*, query, source_types=None, top_k=5, index_path=None):
            return [{"source_type": "brand_voice", "text": "updated", "score": 0.0}]

        monkeypatch.setattr(content_writer, "retrieve", fake_retrieve)

        captured_state: dict[str, Any] = {}

        async def capturing_llm_client(*, state: Any, config: Any) -> FakeLLMResponse:
            captured_state["current_task"] = state.current_task
            captured_state["grounding_context"] = state.scratchpad["grounding_context"]
            return FakeLLMResponse(text="A general post with no invented specifics.", confidence=0.9)

        async def fake_submit_approval_fn(db: Any, **kwargs: Any) -> dict[str, Any]:
            return {"id": "approval-grounding-test"}

        await content_writer.write_post(
            {"topic": "adaptive learning"},
            llm_client=capturing_llm_client,
            submit_approval_fn=fake_submit_approval_fn,
        )

        assert captured_state["grounding_context"] == []
        assert "No genuinely relevant retrieved context was found" in captured_state["current_task"]
        assert "Do not include any specific statistic" in captured_state["current_task"]

    async def test_real_grounding_is_passed_through_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_hit = {"source_type": "brand_voice", "text": "a genuine match", "score": 0.8}

        async def fake_retrieve(*, query, source_types=None, top_k=5, index_path=None):
            return [real_hit]

        monkeypatch.setattr(content_writer, "retrieve", fake_retrieve)

        captured_state: dict[str, Any] = {}

        async def capturing_llm_client(*, state: Any, config: Any) -> FakeLLMResponse:
            captured_state["current_task"] = state.current_task
            captured_state["grounding_context"] = state.scratchpad["grounding_context"]
            return FakeLLMResponse(text="A grounded post.", confidence=0.9)

        async def fake_submit_approval_fn(db: Any, **kwargs: Any) -> dict[str, Any]:
            return {"id": "approval-grounding-test"}

        await content_writer.write_post(
            {"topic": "adaptive learning"},
            llm_client=capturing_llm_client,
            submit_approval_fn=fake_submit_approval_fn,
        )

        assert captured_state["grounding_context"] == [real_hit]
        assert "No genuinely relevant retrieved context" not in captured_state["current_task"]


@pytest.mark.asyncio
async def test_high_confidence_submits_for_approval() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_submit_approval_fn(
        db: Any,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        requested_by_agent: str,
        reason: str,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        calls.append(
            {
                "tool_name": tool_name,
                "arguments": arguments,
                "requested_by_agent": requested_by_agent,
                "reason": reason,
                "confidence": confidence,
            }
        )
        return {"id": "approval-1", "status": "pending"}

    brief = {"topic": "agentic AI trends"}
    result = await content_writer.write_post(
        brief,
        llm_client=_make_llm_client(confidence=0.9),
        submit_approval_fn=fake_submit_approval_fn,
    )

    assert len(calls) == 1
    assert calls[0]["tool_name"] == "publish_post"
    assert calls[0]["arguments"] == {"content": "Drafted LinkedIn post about AI agents."}
    assert calls[0]["requested_by_agent"] == "content_writer"
    assert calls[0]["confidence"] == 0.9
    assert result["status"] == "submitted_for_approval"
    assert result["needs_human_rewrite"] is False
    assert result["tool_name"] == "publish_post"


@pytest.mark.asyncio
async def test_low_confidence_does_not_submit_and_flags_rewrite() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_submit_approval_fn(db: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"id": "approval-2"}

    brief = {"topic": "a topic nobody cares about"}
    result = await content_writer.write_post(
        brief,
        llm_client=_make_llm_client(confidence=0.4),
        submit_approval_fn=fake_submit_approval_fn,
    )

    assert calls == []
    assert result["status"] == "needs_human_rewrite"
    assert result["needs_human_rewrite"] is True
    assert result["confidence"] == 0.4


@pytest.mark.asyncio
async def test_future_target_publish_date_uses_schedule_post() -> None:
    """schedule_post has no working backend (see content_writer.SCHEDULING_SUPPORTED

    and schedule_post.py's docstring) — even a perfectly valid future date
    must not route there, since that approval would be guaranteed to fail
    the moment a human approved it.
    """
    calls: list[dict[str, Any]] = []

    async def fake_submit_approval_fn(
        db: Any,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        requested_by_agent: str,
        reason: str,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        calls.append({"tool_name": tool_name, "arguments": arguments})
        return {"id": "approval-3"}

    future = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0)
    brief = {"topic": "AI news roundup", "target_publish_date": future.isoformat()}
    result = await content_writer.write_post(
        brief,
        llm_client=_make_llm_client(confidence=0.8),
        submit_approval_fn=fake_submit_approval_fn,
    )

    assert len(calls) == 1
    assert calls[0]["tool_name"] == "schedule_post"
    assert calls[0]["arguments"]["content"] == "Drafted LinkedIn post about AI agents."
    assert calls[0]["arguments"]["publish_at"] == future.isoformat()
    assert result["tool_name"] == "schedule_post"


@pytest.mark.asyncio
async def test_future_target_publish_date_uses_schedule_post_once_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Coverage for the currently-dormant path: once SCHEDULING_SUPPORTED

    flips back on (real scheduling infra lands), a valid future date must
    still route to schedule_post with the right arguments — this test
    exists so that flip is verified safe on the day it happens, not
    discovered broken then.
    """
    monkeypatch.setattr(content_writer, "SCHEDULING_SUPPORTED", True)
    calls: list[dict[str, Any]] = []

    async def fake_submit_approval_fn(db: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"id": "approval-3b"}

    future = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0)
    brief = {"topic": "AI news roundup", "target_publish_date": future.isoformat()}
    result = await content_writer.write_post(
        brief,
        llm_client=_make_llm_client(confidence=0.8),
        submit_approval_fn=fake_submit_approval_fn,
    )

    assert len(calls) == 1
    assert calls[0]["tool_name"] == "schedule_post"
    assert calls[0]["arguments"]["content"] == "Drafted LinkedIn post about AI agents."
    assert calls[0]["arguments"]["publish_at"] == future.isoformat()
    assert result["tool_name"] == "schedule_post"


async def test_past_target_publish_date_falls_back_to_publish_post() -> None:
    """A hallucinated/stale target_publish_date (e.g. an LLM guessing a date

    in the past) must never crash or silently produce an unschedulable
    request — it falls back to publishing immediately instead.
    """
    calls: list[dict[str, Any]] = []

    async def fake_submit_approval_fn(db: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"id": "approval-5"}

    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    brief = {"topic": "AI news roundup", "target_publish_date": past}
    result = await content_writer.write_post(
        brief,
        llm_client=_make_llm_client(confidence=0.8),
        submit_approval_fn=fake_submit_approval_fn,
    )

    assert len(calls) == 1
    assert calls[0]["tool_name"] == "publish_post"
    assert "publish_at" not in calls[0]["arguments"]
    assert result["tool_name"] == "publish_post"


def test_resolve_future_publish_at_gives_a_bare_date_a_default_time() -> None:
    future_date = (datetime.now(timezone.utc) + timedelta(days=7)).date()
    resolved = content_writer._resolve_future_publish_at(future_date.isoformat())
    assert resolved is not None
    assert resolved.date() == future_date
    assert resolved.hour == content_writer._DEFAULT_PUBLISH_HOUR_UTC


def test_resolve_future_publish_at_returns_none_for_unparseable_input() -> None:
    assert content_writer._resolve_future_publish_at("not a date") is None


def test_resolve_future_publish_at_returns_none_for_empty_input() -> None:
    assert content_writer._resolve_future_publish_at(None) is None
    assert content_writer._resolve_future_publish_at("") is None


@pytest.mark.asyncio
async def test_confidence_exactly_at_threshold_counts_as_ready() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_submit_approval_fn(db: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"id": "approval-4"}

    brief = {"topic": "boundary case"}
    result = await content_writer.write_post(
        brief,
        llm_client=_make_llm_client(confidence=content_writer.CONFIDENCE_THRESHOLD),
        submit_approval_fn=fake_submit_approval_fn,
    )

    assert len(calls) == 1
    assert result["needs_human_rewrite"] is False
