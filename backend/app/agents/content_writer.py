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
from typing import Any

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
    "generic AI-blog tone, never a claim or statistic that isn't grounded in "
    "retrieved context. After drafting, score your own confidence (0.0 to "
    "1.0) that this draft matches their brand voice and is ready to show the "
    "user. A draft is either ready for human approval or it isn't — there is "
    "no partial credit."
)

register_prompt("content_writer", SYSTEM_PROMPT)

# PRP SAFETY REQUIREMENTS: fixed threshold, not computed. safety-agent
# independently defines the same constant in its own file — both are
# correct, this isn't a race condition since the PRP fixes the value.
CONFIDENCE_THRESHOLD = 0.75

SubmitApprovalFn = Callable[..., Awaitable[Any]]

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

    grounding_context = await retrieve(
        query=topic,
        source_types=["brand_voice", "past_posts", "industry_news"],
        top_k=5,
        index_path=index_path,
    )

    state = AgentState(
        task_id=str(uuid.uuid4()),
        agent_name=RuntimeAgentName.CONTENT_WRITER,
        current_task=f"Write a LinkedIn post about: {topic}",
        scratchpad={"brief": brief, "grounding_context": grounding_context},
    )

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

    tool_name = "schedule_post" if brief.get("target_publish_date") else "publish_post"
    arguments: dict[str, Any] = {"post_content": output.post_content, "topic": output.topic}
    if brief.get("target_publish_date"):
        arguments["target_publish_date"] = brief["target_publish_date"]

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
