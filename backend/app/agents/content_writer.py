"""Content Writer Agent — writes brand-voice-grounded LinkedIn post drafts.

PRP RUNTIME AGENTS #2: turns a Content Strategist brief into full post copy,
grounded in the user's style guide, past posts, and industry news, then
self-scores confidence that the draft matches brand voice. A draft either
clears CONFIDENCE_THRESHOLD and goes to the human-approval queue, or it
doesn't and gets flagged for a human to rewrite by hand — there is no
in-between state.

CLAUDE.md non-negotiable #1: this module never calls llm_client directly.
It goes through harness.loop.run_step, the sole choke point for LLM calls.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timezone
from typing import Any

from app.activity import activity
from app.harness.loop import AgentRunConfig, LLMClient, run_step
from app.harness.state import AgentState, RuntimeAgentName
from app.llmops.model_router import route
from app.llmops.prompt_registry import register_prompt
from app.rag.retrieve import retrieve
from pydantic import BaseModel

SYSTEM_PROMPT = (
    "You are the Content Writer for a professional's LinkedIn presence. Given "
    "a post brief from the Content Strategist, write the full post in the "
    "user's brand voice — grounded in their style guide and past posts, never "
    "generic AI-blog tone.\n\n"
    "Hard rule on specifics: never state a statistic, percentage, survey result, "
    "named company, or 'case study' unless it appears — verbatim or near-verbatim — "
    "in the Retrieved Context you were given. Do not invent a plausible-sounding "
    "number or example to make a point land harder; a real professional publishing "
    "under their own name cannot verify a fact you made up, and a fabricated case "
    "study is a credibility risk to them, not a stylistic flourish. If the Retrieved "
    "Context is empty, thin, or irrelevant to the topic, write from general, "
    "defensible expertise and opinion instead — a post with zero hard numbers is "
    "always safer than one with an invented one.\n\n"
    "After drafting, score your own confidence (0.0 to 1.0) that this draft matches "
    "their brand voice AND is ready to show the user — a draft containing even one "
    "unverifiable specific claim is not ready, regardless of how well it matches "
    "their voice. A draft is either ready for human approval or it isn't — there is "
    "no partial credit."
)

register_prompt("content_writer", SYSTEM_PROMPT)

# PRP SAFETY REQUIREMENTS: fixed threshold, not computed. safety-agent
# independently defines the same constant in its own file — both are
# correct, this isn't a race condition since the PRP fixes the value.
CONFIDENCE_THRESHOLD = 0.75

SubmitApprovalFn = Callable[..., Awaitable[Any]]

# Approved future posts are persisted locally; the scheduled publishing job
# sends them through the same rate-limited LinkedIn publishing implementation.
SCHEDULING_SUPPORTED = True

# LinkedIn publishing requires an hour, not just a day — this is the default
# time of day used when a brief's target_publish_date is a bare date.
_DEFAULT_PUBLISH_HOUR_UTC = 9


def _has_meaningful_grounding(hits: list[dict[str, Any]]) -> bool:
    """True if at least one retrieved hit has a real (nonzero) relevance score.

    rag.retrieve() applies no relevance threshold of its own — it always
    returns the top_k nearest vectors in the index, even when none of them
    are actually related to the query. Verified live: with only unrelated
    leftover brand_voice fragments indexed, retrieve() still returned 5
    "hits" scored 0.0 for a completely unrelated topic, and the model used
    their presence as license to fabricate a case study, self-scoring 1.0
    confidence anyway. A hit scored 0 is retrieval noise, not grounding —
    write_post() must not hand it to the model as if it were.
    """
    return any(hit.get("score", 0) > 0 for hit in hits)


def _resolve_future_publish_at(target_publish_date: Any) -> datetime | None:
    """Turn brief["target_publish_date"] into a concrete future datetime, or None.

    None means "publish now instead" (write_post falls back to publish_post)
    rather than raising — target_publish_date comes from the Content
    Strategist's own LLM call and is never trustworthy enough to let a bad
    value (unparseable, or in the past) crash or silently misfire the
    schedule_post tool's own future-only validation (schedule_post.py's
    SchedulePostArgs). Accepts either a bare ISO date or a full ISO
    datetime — callers have supplied both shapes historically.
    """
    if not target_publish_date:
        return None
    try:
        if isinstance(target_publish_date, datetime):
            parsed = target_publish_date
        elif isinstance(target_publish_date, date):
            parsed = datetime.combine(target_publish_date, time(_DEFAULT_PUBLISH_HOUR_UTC), tzinfo=timezone.utc)
        else:
            text = str(target_publish_date).strip()
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if len(text) <= 10:  # bare "YYYY-MM-DD", no time component parsed
                parsed = datetime.combine(parsed.date(), time(_DEFAULT_PUBLISH_HOUR_UTC), tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed <= datetime.now(timezone.utc):
        return None
    return parsed


CONTENT_WRITER_CONFIG = AgentRunConfig(
    agent_name="content_writer",
    allowed_tools=["search_knowledge_base", "draft_post"],
    model_tier=route("content_writer", "draft").tier.value,
    escalation_condition=lambda state: (
        state.confidence is not None and state.confidence < CONFIDENCE_THRESHOLD
    ),
    task_type="draft",
)


class WriterOutput(BaseModel):
    post_content: str
    confidence: float
    topic: str


async def write_post(
    brief: dict[str, Any],
    llm_client: LLMClient,
    index_path: str | None = None,
    db: Any = None,
    submit_approval_fn: SubmitApprovalFn | None = None,
) -> dict[str, Any]:
    """Draft a post from a Strategist brief and route it by confidence.

    confidence >= CONFIDENCE_THRESHOLD: queued for human approval via
    submit_for_approval (never executed here — approval and execution are
    the safety-agent's and a human's job, respectively).
    confidence < CONFIDENCE_THRESHOLD: flagged needs_human_rewrite, no
    approval submission at all — a different path from the approval queue.
    """
    topic = brief["topic"]

    retrieved = await retrieve(
        query=topic,
        source_types=["brand_voice", "past_posts", "industry_news"],
        top_k=5,
        index_path=index_path,
    )
    has_grounding = _has_meaningful_grounding(retrieved)
    # Score-0 hits are noise (see _has_meaningful_grounding) — don't even show
    # them to the model as "context," rather than trusting the prompt alone to
    # override what it's handed.
    grounding_context = retrieved if has_grounding else []

    current_task = f"Write a LinkedIn post about: {topic}"
    if not has_grounding:
        current_task += (
            " No genuinely relevant retrieved context was found for this topic — write from "
            "general, defensible expertise and opinion only. Do not include any specific "
            "statistic, percentage, named company, or case study in this draft; there is "
            "nothing here to verify one against."
        )

    state = AgentState(
        task_id=str(uuid.uuid4()),
        agent_name=RuntimeAgentName.CONTENT_WRITER,
        current_task=current_task,
        scratchpad={"brief": brief, "grounding_context": grounding_context},
    )

    with activity("content_writer", "writing", detail=f"Drafting a post about {topic!r}"):
        state = await run_step(state, CONTENT_WRITER_CONFIG, llm_client, tool_executor=None)

    confidence = state.confidence if state.confidence is not None else 0.0
    post_content = state.conversation[-1]["content"] if state.conversation else ""
    output = WriterOutput(post_content=post_content, confidence=confidence, topic=topic)

    if output.confidence < CONFIDENCE_THRESHOLD:
        return {
            "status": "needs_human_rewrite",
            "needs_human_rewrite": True,
            "confidence": output.confidence,
            "post_content": output.post_content,
            "topic": output.topic,
        }

    publish_at = _resolve_future_publish_at(brief.get("target_publish_date"))
    if publish_at is not None and SCHEDULING_SUPPORTED:
        tool_name = "schedule_post"
        arguments: dict[str, Any] = {"content": output.post_content, "publish_at": publish_at.isoformat()}
    else:
        tool_name = "publish_post"
        arguments = {"content": output.post_content}

    if submit_approval_fn is not None:
        approval_fn = submit_approval_fn
    else:
        # Lazy import (not at module top): safety-agent owns approval_gate.py
        # and is building it in parallel — it may not exist on disk yet when
        # THIS module is imported. Deferring the import into the function
        # body means it only needs to exist by the time write_post() actually
        # runs, not at import time, so neither build order can crash the other.
        from app.safety.approval_gate import (  # type: ignore[no-redef]
            submit_for_approval as approval_fn,
        )

    approval_request = await approval_fn(
        db,
        tool_name=tool_name,
        arguments=arguments,
        requested_by_agent="content_writer",
        reason="brand-voice-grounded draft ready for review",
        confidence=output.confidence,
    )

    return {
        "status": "submitted_for_approval",
        "needs_human_rewrite": False,
        "tool_name": tool_name,
        "confidence": output.confidence,
        "post_content": output.post_content,
        "topic": output.topic,
        "approval_request": approval_request,
    }
