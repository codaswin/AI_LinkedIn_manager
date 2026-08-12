from __future__ import annotations

from dataclasses import dataclass, field
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
async def test_target_publish_date_submits_schedule_post() -> None:
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

    brief = {"topic": "AI news roundup", "target_publish_date": "2026-08-20T09:00:00Z"}
    result = await content_writer.write_post(
        brief,
        llm_client=_make_llm_client(confidence=0.8),
        submit_approval_fn=fake_submit_approval_fn,
    )

    assert len(calls) == 1
    assert calls[0]["tool_name"] == "schedule_post"
    assert calls[0]["arguments"]["target_publish_date"] == "2026-08-20T09:00:00Z"
    assert result["tool_name"] == "schedule_post"


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
