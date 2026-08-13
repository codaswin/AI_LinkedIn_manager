from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from app.harness.loop import ToolCallRequest
from app.harness.state import AgentState
from app.llmops.prompt_registry import get_prompt, register_prompt
from evals.llm_judge import JUDGE_SYSTEM_PROMPT, judge_post, judge_reply


@pytest.fixture(autouse=True)
def _ensure_prompt_registered() -> None:
    register_prompt("evals", JUDGE_SYSTEM_PROMPT)


@dataclass
class FakeLLMResponse:
    text: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    cost_usd: float = 0.0
    confidence: float | None = None
    goal_achieved: bool = True


def _make_llm_client(text: str):
    async def _client(*, state: AgentState, config: Any) -> FakeLLMResponse:
        return FakeLLMResponse(text=text)

    return _client


POST_CASE = {
    "id": "post-001",
    "topic": "Launching our research assistant",
    "angle": "Focus on the problem it solves",
    "must_avoid": ["generic AI-blog tone"],
}

REPLY_CASE = {
    "id": "reply-001",
    "notification": {"type": "comment", "text": "How do you handle rate limits?"},
}


def test_system_prompt_is_registered() -> None:
    assert get_prompt("evals") == JUDGE_SYSTEM_PROMPT
    assert "length" in JUDGE_SYSTEM_PROMPT.lower()


async def test_judge_post_returns_parsed_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def spying_client(*, state: AgentState, config: Any) -> FakeLLMResponse:
        captured["state"] = state
        captured["config"] = config
        return FakeLLMResponse(
            text=json.dumps({"brand_voice_fidelity": 4, "groundedness": 5, "reasoning": "solid, grounded"})
        )

    result = await judge_post(POST_CASE, "A grounded post about our research assistant.", spying_client)

    assert result == {"brand_voice_fidelity": 4, "groundedness": 5, "reasoning": "solid, grounded"}
    assert isinstance(captured["state"], AgentState)
    assert captured["config"].agent_name == "evals"
    assert captured["config"].task_type == "judge"
    assert captured["config"].allowed_tools == []


async def test_judge_post_routes_through_run_step_not_llm_client_directly() -> None:
    """project invariant #1 regression guard, mirroring

    test_analytics.py's equivalent test: the judge must receive a real
    AgentState/AgentRunConfig (run_step's signature), not a bespoke
    keyword-argument shape a direct call would use.
    """
    llm_client = _make_llm_client(json.dumps({"brand_voice_fidelity": 3, "groundedness": 3, "reasoning": "ok"}))
    result = await judge_post(POST_CASE, "some post content", llm_client)
    assert result["brand_voice_fidelity"] == 3


async def test_judge_post_raises_on_non_json_response() -> None:
    llm_client = _make_llm_client("not json at all")
    with pytest.raises(ValueError, match="not valid JSON"):
        await judge_post(POST_CASE, "some post content", llm_client)


async def test_judge_post_raises_on_empty_response() -> None:
    llm_client = _make_llm_client("")
    with pytest.raises(ValueError, match="empty response"):
        await judge_post(POST_CASE, "some post content", llm_client)


async def test_judge_post_raises_when_response_is_not_a_json_object() -> None:
    llm_client = _make_llm_client(json.dumps([1, 2, 3]))
    with pytest.raises(ValueError, match="must be a JSON object"):
        await judge_post(POST_CASE, "some post content", llm_client)


async def test_judge_reply_returns_parsed_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def spying_client(*, state: AgentState, config: Any) -> FakeLLMResponse:
        captured["state"] = state
        return FakeLLMResponse(
            text=json.dumps({"reply_appropriateness": 5, "brand_voice_fidelity": 4, "reasoning": "on point"})
        )

    result = await judge_reply(REPLY_CASE, "Great question — we cap requests per source.", spying_client)

    assert result == {"reply_appropriateness": 5, "brand_voice_fidelity": 4, "reasoning": "on point"}
    assert captured["state"].scratchpad["original_text"] == "How do you handle rate limits?"
    assert captured["state"].scratchpad["draft_reply"] == "Great question — we cap requests per source."


async def test_judge_reply_raises_on_non_json_response() -> None:
    llm_client = _make_llm_client("nope")
    with pytest.raises(ValueError):
        await judge_reply(REPLY_CASE, "some reply", llm_client)
