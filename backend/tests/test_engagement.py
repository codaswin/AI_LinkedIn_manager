from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from app.agents import engagement
from app.harness.loop import ToolCallRequest
from app.llmops import prompt_registry
from app.llmops.model_router import route
from app.tenancy import context as tenancy_context


@pytest.fixture(autouse=True)
def _tenancy_context():
    token = tenancy_context.set_current_user_id("user-engagement-test")
    yield
    tenancy_context.reset_current_user_id(token)


@dataclass
class FakeLLMResponse:
    text: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    cost_usd: float = 0.0
    confidence: float | None = None
    goal_achieved: bool = False


def make_llm_client(text: str = "drafted reply", confidence: float | None = None):
    async def _client(*, state: Any, config: Any) -> FakeLLMResponse:
        return FakeLLMResponse(text=text, confidence=confidence)

    return _client


def fail_if_called_llm_client():
    async def _client(*, state: Any, config: Any) -> FakeLLMResponse:
        raise AssertionError("llm_client should not have been called")

    return _client


def make_recorder():
    calls: list[dict[str, Any]] = []

    async def _fn(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, "kwargs": kwargs})
        return {"recorded": True}

    return _fn, calls


COMMENT_NOTIFICATION = {
    "id": "notif-1",
    "type": "comment",
    "comment_id": "comment-1",
    "text": "Great insight, thanks for sharing!",
}

DM_NOTIFICATION = {
    "id": "notif-2",
    "type": "dm",
    "thread_id": "thread-1",
    "text": "Hey, would love to connect and chat about this.",
}

CONNECTION_NOTIFICATION = {
    "id": "notif-3",
    "type": "connection_request",
    "profile_id": "profile-1",
    "text": "Hi, I'd like to add you to my professional network.",
}


class TestRefusalTopicEscalation:
    @pytest.mark.asyncio
    async def test_refusal_topic_match_escalates_and_skips_approval(self) -> None:
        submit_approval_fn, approval_calls = make_recorder()
        escalate_fn, escalate_calls = make_recorder()

        def refusal_check_fn(text: str) -> str | None:
            return "disparagement of a named individual or competitor"

        result = await engagement.handle_notification(
            COMMENT_NOTIFICATION,
            fail_if_called_llm_client(),
            submit_approval_fn=submit_approval_fn,
            refusal_check_fn=refusal_check_fn,
            escalate_fn=escalate_fn,
        )

        assert result["status"] == "escalated"
        assert result["reason"] == "refusal_topic"
        assert len(escalate_calls) == 1
        assert escalate_calls[0]["kwargs"]["agent_name"] == "engagement"
        assert "disparagement" in escalate_calls[0]["kwargs"]["reason"]
        assert approval_calls == []


class TestLowConfidenceEscalation:
    @pytest.mark.asyncio
    async def test_confidence_below_threshold_escalates_not_approves(self, tmp_path) -> None:
        submit_approval_fn, approval_calls = make_recorder()
        escalate_fn, escalate_calls = make_recorder()

        result = await engagement.handle_notification(
            COMMENT_NOTIFICATION,
            make_llm_client(confidence=0.5),
            index_path=str(tmp_path / "idx"),
            submit_approval_fn=submit_approval_fn,
            refusal_check_fn=lambda text: None,
            escalate_fn=escalate_fn,
        )

        assert result["status"] == "escalated"
        assert result["reason"] == "low_confidence"
        assert result["confidence"] == 0.5
        assert len(escalate_calls) == 1
        assert approval_calls == []


class TestConfidentDraftGoesToApproval:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "notification,expected_tool",
        [
            (COMMENT_NOTIFICATION, "reply_to_comment"),
            (DM_NOTIFICATION, "reply_to_dm"),
            (CONNECTION_NOTIFICATION, "send_connection_request"),
        ],
    )
    async def test_high_confidence_submits_for_approval_with_correct_tool(
        self, notification: dict[str, Any], expected_tool: str, tmp_path
    ) -> None:
        submit_approval_fn, approval_calls = make_recorder()
        escalate_fn, escalate_calls = make_recorder()

        result = await engagement.handle_notification(
            notification,
            make_llm_client(text="a fine draft", confidence=0.9),
            index_path=str(tmp_path / "idx"),
            submit_approval_fn=submit_approval_fn,
            refusal_check_fn=lambda text: None,
            escalate_fn=escalate_fn,
        )

        assert result["status"] == "submitted_for_approval"
        assert result["tool_name"] == expected_tool
        assert escalate_calls == []
        assert len(approval_calls) == 1

        call = approval_calls[0]
        assert call["kwargs"]["tool_name"] == expected_tool
        assert call["kwargs"]["requested_by_agent"] == "engagement"
        assert call["kwargs"]["confidence"] == 0.9
        assert call["kwargs"]["arguments"]


class TestLikeRelevantPost:
    @pytest.mark.asyncio
    async def test_like_relevant_post_calls_execute_tool_directly(self, monkeypatch) -> None:
        calls: list[dict[str, Any]] = []

        async def fake_execute_tool(tool_name: str, raw_arguments: dict[str, Any], approved: bool = False) -> dict[str, Any]:
            calls.append({"tool_name": tool_name, "raw_arguments": raw_arguments, "approved": approved})
            return {"status": "success", "post_id": raw_arguments["post_id"]}

        monkeypatch.setattr(engagement, "execute_tool", fake_execute_tool)

        result = await engagement.like_relevant_post("post-123")

        assert result == {"status": "success", "post_id": "post-123"}
        assert len(calls) == 1
        assert calls[0]["tool_name"] == "like_post"
        assert calls[0]["raw_arguments"] == {"post_id": "post-123"}
        assert calls[0]["approved"] is False


class TestTriageNotification:
    @pytest.mark.asyncio
    async def test_triage_uses_worker_tier_and_returns_classification(self) -> None:
        result = await engagement.triage_notification(
            COMMENT_NOTIFICATION, make_llm_client(text="priority: normal, type: comment")
        )

        assert result["notification_type"] == "comment"
        assert result["classification"] == "priority: normal, type: comment"
        assert result["model_tier"] == route("engagement", "triage").tier.value


class TestPromptAndConfig:
    def test_prompt_registered(self) -> None:
        assert prompt_registry.get_prompt("engagement") == engagement.ENGAGEMENT_SYSTEM_PROMPT
        assert "political endorsements" in prompt_registry.get_prompt("engagement")

    def test_config_resolves_correctly(self) -> None:
        config = engagement.ENGAGEMENT_CONFIG
        assert config.agent_name == "engagement"
        assert config.allowed_tools == [
            "get_linkedin_notifications",
            "search_knowledge_base",
            "like_post",
            "reply_to_comment",
            "reply_to_dm",
            "send_connection_request",
        ]
        assert config.model_tier == route("engagement", "draft").tier.value

    def test_escalation_condition_triggers_below_threshold_only(self) -> None:
        from app.harness.state import AgentState, RuntimeAgentName

        state_low = AgentState(
            task_id="t1", agent_name=RuntimeAgentName.ENGAGEMENT, current_task="x", confidence=0.5
        )
        state_high = AgentState(
            task_id="t2", agent_name=RuntimeAgentName.ENGAGEMENT, current_task="x", confidence=0.9
        )
        state_none = AgentState(task_id="t3", agent_name=RuntimeAgentName.ENGAGEMENT, current_task="x")

        assert engagement.ENGAGEMENT_CONFIG.escalation_condition(state_low) is True
        assert engagement.ENGAGEMENT_CONFIG.escalation_condition(state_high) is False
        assert engagement.ENGAGEMENT_CONFIG.escalation_condition(state_none) is False
