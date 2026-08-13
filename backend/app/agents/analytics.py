"""Analytics & Reporting Agent — weekly performance digest + gated delete suggestions.

runtime-agent requirements §4: weekly digest of impressions/engagement rate/follower delta, flags
underperforming/stale/reputationally-risky posts for deletion. Escalation condition per the product spec:
"Any delete_post suggestion always routes to the approval queue ... regardless of confidence —
never auto-executed." That rule is enforced inside suggest_deletion() itself (see its docstring)
rather than via AgentRunConfig.escalation_condition, because it is specific to one tool
(delete_post) and unconditional — the generic per-run escalation_condition mechanism in
harness.loop is a confidence/state predicate evaluated once per run_step, which is the wrong
shape for "this one tool call always escalates, full stop, no predicate to evaluate."

COORDINATION CONTRACT with the (parallel) safety-agent: safety-agent owns
app.safety.approval_gate.submit_for_approval(db, *, tool_name, arguments, requested_by_agent,
reason, confidence=None). That module may not exist yet at import time, so it is never imported
at module level here — only lazily, inside suggest_deletion(), and only when the caller hasn't
already injected a submit_approval_fn (tests always inject one).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from app.activity import activity
from app.harness.loop import AgentRunConfig, LLMClient, run_step
from app.harness.state import AgentState, RuntimeAgentName
from app.llmops.model_router import route
from app.llmops.prompt_registry import register_prompt
from app.tools.registry import execute_tool
from pydantic import BaseModel

AGENT_NAME = "analytics"

ANALYTICS_SYSTEM_PROMPT = (
    "You are the Analytics & Reporting Agent for a professional's LinkedIn presence. Once a "
    "week, summarize post performance — impressions, engagement rate, follower delta — into a "
    "digest for the user. While reviewing, flag any post that is significantly underperforming, "
    "stale, or reputationally risky, and suggest it for deletion. A deletion suggestion is never "
    "something you decide alone: always route it to the human approval queue with the full post "
    "content, publish date, and engagement stats attached, so the human sees exactly what they'd "
    "be deleting. Never suggest deleting more than one post per recommendation, and never suggest "
    "a delete based on incomplete data."
)

register_prompt(AGENT_NAME, ANALYTICS_SYSTEM_PROMPT)

# Escalation is handled inside suggest_deletion(), not via this config (see module docstring) —
# the digest path itself is purely informational and has nothing to escalate.
ANALYTICS_AGENT_CONFIG = AgentRunConfig(
    agent_name=AGENT_NAME,
    allowed_tools=["generate_analytics_report", "search_knowledge_base", "delete_post"],
    model_tier=route(AGENT_NAME, "summarize").tier.value,
    escalation_condition=None,
    task_type="summarize",
)


class WeeklyDigest(BaseModel):
    period_start: date
    period_end: date
    total_impressions: int
    avg_engagement_rate: float
    follower_delta: int
    flagged_posts: list[dict[str, str]]


SubmitApprovalFn = Callable[..., Awaitable[Any]]


def _parse_digest_summary(response_text: str) -> dict[str, Any]:
    if not response_text:
        return {}
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"analytics LLM response was not valid digest-summary JSON: {response_text!r}"
        ) from exc
    return payload if isinstance(payload, dict) else {}


async def generate_weekly_digest(
    db: Any,
    llm_client: LLMClient,
    period_start: date,
    period_end: date,
) -> WeeklyDigest:
    """Purely informational: no approval gate on the digest itself.

    `db` is accepted (and threaded through for a future real implementation that queries episodic
    memory directly) but unused today — generate_analytics_report is currently a stub tool with no
    memory-agent wiring yet (see its own TODO); this function's contract does not change when that
    lands.

    The only judgment call here — which posts look underperforming/stale/risky enough to flag — is
    made by the LLM, routed through harness.loop.run_step() (project invariant #1: no module
    may hold an LLM client and invoke it directly). Numeric digest fields come straight from the
    tool's report (deterministic, stored engagement data), never re-derived by the LLM.
    """
    report = await execute_tool(
        "generate_analytics_report",
        {"period_start": period_start, "period_end": period_end},
        approved=False,
    )
    if report.get("status") != "success":
        raise RuntimeError(f"generate_analytics_report did not succeed: {report}")

    report_data: dict[str, Any] = report.get("result") or {}

    state = AgentState(
        task_id="analytics-weekly-digest",
        agent_name=RuntimeAgentName.ANALYTICS_REPORTING,
        current_task=(
            "Given this generate_analytics_report output, identify which posts (if any) are "
            "significantly underperforming, stale, or reputationally risky enough to flag for "
            "possible deletion. Respond with a JSON object: "
            '{"flagged_posts": [{"post_id": ..., "reason": ...}, ...]}. '
            "An empty list is a valid answer — do not flag a post without a clear reason."
        ),
        scratchpad={
            "report": report_data,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
        },
    )
    with activity("analytics", "analyzing", detail="Reviewing this week's post performance"):
        state = await run_step(state, ANALYTICS_AGENT_CONFIG, llm_client, tool_executor=None)
    response_text = state.conversation[-1]["content"] if state.conversation else ""
    summary = _parse_digest_summary(response_text)

    return WeeklyDigest(
        period_start=period_start,
        period_end=period_end,
        total_impressions=int(report_data.get("impressions", 0)),
        avg_engagement_rate=float(report_data.get("engagement_rate", 0.0)),
        follower_delta=int(report_data.get("follower_delta", 0)),
        flagged_posts=list(summary.get("flagged_posts", [])),
    )


async def suggest_deletion(
    post_id: str,
    post_content: str,
    published_at: datetime,
    engagement_stats: dict[str, int | float],
    reason: str,
    db: Any = None,
    submit_approval_fn: SubmitApprovalFn | None = None,
) -> Any:
    """Route exactly one post's delete suggestion to the human approval queue.

    Unconditional: there is no confidence parameter and no confidence-based skip anywhere in this
    function. Content Writer/Engagement may skip the approval queue when confidence is low —
    Analytics never does, for a delete suggestion, because deletion is irreversible (product spec SAFETY
    REQUIREMENTS + project invariant #3). Refuses to submit at all rather than submit a
    partial payload: a human approving an irreversible delete must see the full post content,
    publish date, and engagement stats, not a bare post_id.
    """
    missing = [
        name
        for name, value in (
            ("post_content", post_content),
            ("published_at", published_at),
            ("engagement_stats", engagement_stats),
            ("reason", reason),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "suggest_deletion refuses to submit an incomplete delete suggestion — missing: "
            + ", ".join(missing)
        )

    if submit_approval_fn is None:
        from app.safety.approval_gate import submit_for_approval as _submit_for_approval

        submit_approval_fn = _submit_for_approval

    return await submit_approval_fn(
        db,
        tool_name="delete_post",
        arguments={
            "post_id": post_id,
            "post_content": post_content,
            "published_at": published_at,
            "engagement_stats": engagement_stats,
        },
        requested_by_agent=AGENT_NAME,
        reason=reason,
    )
