"""Engagement Agent — monitors comments/DMs/connection requests, drafts
replies, and likes relevant feed posts within safety-agent's gates.

COORDINATION CONTRACT with the (parallel) safety-agent build: neither
app.safety.approval_gate nor app.safety.guardrails may be imported at module
level here, since either may not exist yet while safety-agent's build is in
flight. Every call into them happens via a lazy import *inside* the function
body that needs it, behind an injectable `*_fn` parameter — callers (and this
module's own tests) always pass fakes rather than depending on the real
safety module being finished.

CLAUDE.md non-negotiable #1: every LLM call goes through harness.loop's
run_step() — this module never calls the injected `llm_client` directly, it
only ever hands it to run_step().
"""

from __future__ import annotations

import inspect
import typing as t
from collections.abc import Awaitable, Callable

from app.harness.loop import AgentRunConfig, LLMClient, run_step
from app.harness.state import AgentState, RuntimeAgentName
from app.llmops import prompt_registry
from app.llmops.model_router import route
from app.rag.retrieve import retrieve
from app.tools.registry import execute_tool

# PRP SAFETY REQUIREMENTS: fixed value, duplicated from safety-agent's
# guardrails.CONFIDENCE_THRESHOLD by design (a fixed constant, not something
# computed by the other agent's build, so duplicating it here is safe).
CONFIDENCE_THRESHOLD: float = 0.75

ENGAGEMENT_SYSTEM_PROMPT = (
    "You are the Engagement Agent for a professional's LinkedIn presence. "
    "You monitor comments, DMs, and connection requests, and you scan the "
    "feed for posts worth liking to build visibility. For every "
    "notification: first check if it touches a sensitive topic (political "
    "endorsements, health/financial/legal advice, disparaging a named "
    "person or competitor, engagement-bait/misinformation) — if so, "
    "escalate to a human immediately, do not draft anything. Otherwise, "
    "draft an appropriate reply or action and score your confidence (0.0 "
    "to 1.0) that it's ready to send as-is. Liking a post is low-risk and "
    "can happen directly within the daily cap; replying, messaging, or "
    "connecting always needs a human's sign-off first, no matter how "
    "confident you are."
)

prompt_registry.register_prompt("engagement", ENGAGEMENT_SYSTEM_PROMPT)

_NOTIFICATION_TYPE_TO_TOOL: dict[str, str] = {
    "comment": "reply_to_comment",
    "dm": "reply_to_dm",
    "connection_request": "send_connection_request",
}

ENGAGEMENT_CONFIG = AgentRunConfig(
    agent_name="engagement",
    allowed_tools=[
        "get_linkedin_notifications",
        "search_knowledge_base",
        "like_post",
        "reply_to_comment",
        "reply_to_dm",
        "send_connection_request",
    ],
    model_tier=route("engagement", "draft").tier.value,
    escalation_condition=lambda state: (
        state.confidence is not None and state.confidence < CONFIDENCE_THRESHOLD
    ),
)


def _resolve_tool_name(notification_type: str) -> str:
    try:
        return _NOTIFICATION_TYPE_TO_TOOL[notification_type]
    except KeyError as exc:
        raise ValueError(f"Unknown notification type: {notification_type!r}") from exc


def _build_tool_arguments(
    notification_type: str, notification: dict[str, t.Any], draft_text: str
) -> dict[str, t.Any]:
    notification_id = notification.get("id")
    if notification_type == "comment":
        return {
            "comment_id": notification.get("comment_id", notification_id),
            "reply_text": draft_text,
        }
    if notification_type == "dm":
        return {
            "thread_id": notification.get("thread_id", notification_id),
            "message_text": draft_text,
        }
    if notification_type == "connection_request":
        return {
            "profile_id": notification.get("profile_id", notification_id),
            "note": draft_text,
        }
    raise ValueError(f"Unknown notification type: {notification_type!r}")


async def _maybe_await(result: t.Any) -> t.Any:
    if inspect.isawaitable(result):
        return await result
    return result


async def triage_notification(notification: dict[str, t.Any], llm_client: LLMClient) -> dict[str, t.Any]:
    """Cheap worker-tier pass: classify a raw notification by type/priority.

    High-volume, low-stakes step, so it stays a single run_step() call with
    no tools and no RAG grounding — just enough for the caller to decide
    which notifications are worth the (more expensive) handle_notification
    drafting pass.
    """
    tier = route("engagement", "triage")
    state = AgentState(
        task_id=f"triage-{notification.get('id', 'unknown')}",
        agent_name=RuntimeAgentName.ENGAGEMENT,
        current_task=f"Triage a LinkedIn {notification.get('type', 'unknown')} notification",
        scratchpad={"notification": notification},
    )
    config = AgentRunConfig(
        agent_name="engagement",
        allowed_tools=[],
        model_tier=tier.tier.value,
        task_type="triage",
    )
    state = await run_step(state, config, llm_client)
    classification = state.conversation[-1]["content"] if state.conversation else None

    return {
        "notification_type": notification.get("type"),
        "classification": classification,
        "priority_confidence": state.confidence,
        "model_tier": tier.tier.value,
    }


async def handle_notification(
    notification: dict[str, t.Any],
    llm_client: LLMClient,
    index_path: str | None = None,
    db: t.Any = None,
    submit_approval_fn: Callable[..., Awaitable[t.Any]] | None = None,
    refusal_check_fn: Callable[[str], str | None] | None = None,
    escalate_fn: Callable[..., t.Any] | None = None,
) -> dict[str, t.Any]:
    """Draft-and-gate pipeline for one notification.

    Refusal-topic match -> escalate, no draft. Otherwise draft (RAG-grounded,
    primary tier) and score confidence: below threshold -> escalate, at or
    above -> submit to the human approval queue. Never executes a gated tool
    itself (CLAUDE.md non-negotiable #3).
    """
    if refusal_check_fn is None:
        from app.safety.guardrails import matches_refusal_topic

        refusal_check_fn = matches_refusal_topic
    if escalate_fn is None:
        from app.safety.guardrails import escalate_to_human

        escalate_fn = escalate_to_human
    if submit_approval_fn is None:
        from app.safety.approval_gate import submit_for_approval

        submit_approval_fn = submit_for_approval

    notification_type = notification.get("type", "")
    notification_text = notification.get("text", "")

    topic = refusal_check_fn(notification_text)
    if topic is not None:
        await _maybe_await(
            escalate_fn(
                agent_name="engagement",
                reason=f"refusal topic matched: {topic}",
                context={"notification": notification, "matched_topic": topic},
            )
        )
        return {"status": "escalated", "reason": "refusal_topic", "topic": topic}

    retrieved_context = await retrieve(
        query=notification_text,
        source_types=["comment_dm_threads", "brand_voice"],
        top_k=5,
        index_path=index_path,
    )

    tier = route("engagement", "draft")
    state = AgentState(
        task_id=f"draft-{notification.get('id', 'unknown')}",
        agent_name=RuntimeAgentName.ENGAGEMENT,
        current_task=f"Draft a reply for a LinkedIn {notification_type} notification",
        scratchpad={"notification": notification, "retrieved_context": retrieved_context},
    )
    config = AgentRunConfig(
        agent_name="engagement",
        allowed_tools=["search_knowledge_base"],
        model_tier=tier.tier.value,
        task_type="draft",
    )
    state = await run_step(state, config, llm_client)

    draft_text = state.conversation[-1]["content"] if state.conversation else ""
    confidence = state.confidence if state.confidence is not None else 0.0

    if confidence < CONFIDENCE_THRESHOLD:
        await _maybe_await(
            escalate_fn(
                agent_name="engagement",
                reason=f"reply confidence {confidence:.2f} below threshold {CONFIDENCE_THRESHOLD}",
                context={"notification": notification, "draft": draft_text, "confidence": confidence},
            )
        )
        return {"status": "escalated", "reason": "low_confidence", "confidence": confidence}

    tool_name = _resolve_tool_name(notification_type)
    arguments = _build_tool_arguments(notification_type, notification, draft_text)

    approval_request = await submit_approval_fn(
        db,
        tool_name=tool_name,
        arguments=arguments,
        requested_by_agent="engagement",
        reason=f"drafted {notification_type} reply, confidence {confidence:.2f}",
        confidence=confidence,
    )

    return {
        "status": "submitted_for_approval",
        "tool_name": tool_name,
        "confidence": confidence,
        "approval_request": approval_request,
    }


async def like_relevant_post(post_id: str) -> dict[str, t.Any]:
    """`like_post` requires no approval — the daily rate cap is enforced

    inside the tool itself (Phase 1), so this just executes it directly.
    """
    from app.tools import like_post  # noqa: F401 — registers the tool on import

    return await execute_tool("like_post", {"post_id": post_id}, approved=False)
