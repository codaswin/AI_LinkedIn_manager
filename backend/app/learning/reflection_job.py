"""Reflection job — periodic (not real-time) analysis of recent negative

feedback into proposed, reviewable changes. Run via a scheduled job (cron,
Celery beat, ...) — weekly or after N new feedback entries, never inline in
a request path (skills/LEARNING.md Best Practices). No scheduler is wired
up here; that's deployment/ops, out of this module's scope.

project invariant #1: this module never calls an LLM client directly
— it goes through harness.loop.run_step(), exactly like every runtime
agent's own LLM calls.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.activity import activity
from app.harness.loop import AgentRunConfig, LLMClient, run_step
from app.harness.state import AgentState, RuntimeAgentName
from app.learning.feedback import recent_negative_feedback
from app.learning.proposal_review import submit_proposal
from app.llmops.model_router import route
from app.llmops.prompt_registry import register_prompt
from app.models.feedback import FeedbackRecord
from sqlalchemy.ext.asyncio import AsyncSession

REFLECTION_SYSTEM_PROMPT = (
    "You are analyzing recent negative feedback (rejected or human-edited agent drafts) on an "
    "AI LinkedIn management system to find patterns worth acting on. Identify up to 3 concrete, "
    "specific patterns — not vague generalities — and for each propose ONE targeted change. "
    "Categorize each proposed change as exactly one of: 'retrieval_weight' (a numeric "
    "retrieval-ranking tweak) or 'few_shot_example' (an additive example pulled from a "
    "top-performing past post) — both safe to auto-apply only at high confidence — or "
    "'system_prompt', 'brand_voice_profile', 'new_tool', 'approval_gating_rule', or "
    "'confidence_threshold', which ALWAYS require human review regardless of confidence. "
    'Respond as a JSON array: [{"pattern": "...", "change_type": "...", "proposed_change": '
    '"...", "confidence": <0.0-1.0>}]. An empty array is a valid answer if nothing concrete '
    "stands out — never invent a pattern to fill the list."
)

register_prompt("learning", REFLECTION_SYSTEM_PROMPT)

# Judgment call, mirroring skills/LEARNING.md's own precedent ("if len(feedback)
# < 5: return"): fewer entries than this isn't enough signal to distinguish a
# real pattern from noise — skipping is better than manufacturing insight
# from 1-2 data points.
MIN_FEEDBACK_FOR_REFLECTION = 5


def _reflection_config() -> AgentRunConfig:
    """Resolved fresh on every call, matching every other agent's

    build_run_config() in this codebase.
    """
    return AgentRunConfig(
        agent_name="learning",
        allowed_tools=[],
        model_tier=route("learning", "reflect").tier.value,
        escalation_condition=None,
        task_type="reflect",
    )


def _format_feedback_entries(feedback: list[FeedbackRecord]) -> str:
    return "\n".join(f"- [{f.agent_name}/{f.signal_type}] {f.detail}" for f in feedback)


def _parse_proposals(response_text: str) -> list[dict[str, Any]]:
    if not response_text:
        return []
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"reflection job response was not valid JSON: {response_text!r}") from exc
    if not isinstance(payload, list):
        # ValueError (not TypeError) to match every other "malformed LLM JSON
        # output" raise in this codebase (research.py, research_pipeline.py,
        # content_strategist.py, analytics.py).
        raise ValueError(f"reflection job response must be a JSON array: {response_text!r}")  # noqa: TRY004
    return payload


async def run_reflection(db: AsyncSession, llm_client: LLMClient, days: int = 7) -> dict[str, Any]:
    """Analyze the last `days` of negative feedback and submit a proposal for

    each pattern found. Returns a summary even when skipped or when nothing
    concrete was found, so a scheduler can log/observe every run uniformly
    rather than distinguishing "skipped" from "silent."
    """
    feedback = await recent_negative_feedback(db, days=days)
    if len(feedback) < MIN_FEEDBACK_FOR_REFLECTION:
        return {"ran": False, "reason": "insufficient_feedback", "feedback_count": len(feedback), "proposals": []}

    state = AgentState(
        task_id=str(uuid.uuid4()),
        agent_name=RuntimeAgentName.LEARNING,
        current_task="Analyze recent negative feedback and propose up to 3 concrete changes.",
        scratchpad={"feedback_entries": _format_feedback_entries(feedback), "feedback_count": len(feedback)},
    )
    with activity("learning", "reflecting", detail="Analyzing recent feedback for patterns"):
        state = await run_step(state, _reflection_config(), llm_client, tool_executor=None)
    response_text = state.conversation[-1]["content"] if state.conversation else "[]"
    raw_proposals = _parse_proposals(response_text)

    submitted = [
        await submit_proposal(
            db,
            pattern=p["pattern"],
            change_type=p["change_type"],
            proposed_change=p["proposed_change"],
            confidence=float(p["confidence"]),
        )
        for p in raw_proposals
    ]

    return {
        "ran": True,
        "feedback_count": len(feedback),
        "proposal_count": len(submitted),
        "proposals": submitted,
    }
