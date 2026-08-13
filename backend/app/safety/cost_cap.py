"""Single call site that composes the two already-built hard-stop systems.

This module does NOT reimplement cost or rate limiting:
- llmops/cost_tracker.py already hard-stops LLM spend at LLM_COST_BUDGET_DAILY_USD
  (check_budget() raises CostBudgetExceededError once the daily cap is reached).
- tools/rate_limit.py's DailyRateLimiter already hard-stops per-action daily
  volume (check_and_increment() raises RateLimitExceededError once an
  action's cap is reached).

Both are hard stops today, not warnings — check_all_caps() below is a
convenience wrapper around that existing enforcement, not a new enforcement
mechanism. It intentionally never calls check_and_increment() itself: that
method both checks AND consumes a slot, so calling it from a pre-flight
check (in addition to the tool's own call inside its execute()) would
silently halve the effective daily allowance. Instead it uses
DailyRateLimiter.peek(), a read-only sibling added alongside
check_and_increment() for exactly this purpose.
"""

from __future__ import annotations

import structlog
from app.llmops import cost_tracker
from app.tools.rate_limit import RateLimitExceededError, daily_rate_limiter

logger = structlog.get_logger(__name__)

# action -> (DailyRateLimiter action key, env var), mirrored from each gated
# tool's own RATE_LIMIT_ENV_VAR (backend/app/tools/*.py). reply_to_comment and
# reply_to_dm share one cap, per PRP: "<=20 comment/DM replies/day".
_ACTION_RATE_LIMITS: dict[str, tuple[str, str]] = {
    "publish_post": ("publish_post", "LINKEDIN_API_RATE_LIMIT_POSTS_DAILY"),
    "schedule_post": ("schedule_post", "LINKEDIN_API_RATE_LIMIT_POSTS_DAILY"),
    "delete_post": ("delete_post", "LINKEDIN_API_RATE_LIMIT_DELETES_DAILY"),
    "reply_to_comment": ("reply_to_comment_or_dm", "LINKEDIN_API_RATE_LIMIT_REPLIES_DAILY"),
    "reply_to_dm": ("reply_to_comment_or_dm", "LINKEDIN_API_RATE_LIMIT_REPLIES_DAILY"),
    "send_connection_request": ("send_connection_request", "LINKEDIN_API_RATE_LIMIT_CONNECTIONS_DAILY"),
    "like_post": ("like_post", "LINKEDIN_API_RATE_LIMIT_LIKES_DAILY"),
}


def check_all_caps(agent_name: str, action: str) -> None:
    """Raise if the LLM daily spend cap OR `action`'s daily rate cap is already exceeded.

    Call this before doing risky work (e.g. before an agent even attempts to
    draft something that would result in a gated tool call), in addition to —
    not instead of — the enforcement each underlying system already performs
    at its own call site.
    """
    cost_tracker.check_budget()

    mapping = _ACTION_RATE_LIMITS.get(action)
    if mapping is None:
        logger.debug("cost_cap_no_rate_limit_mapping", agent_name=agent_name, action=action)
        return

    rate_action, env_var = mapping
    used, cap = daily_rate_limiter.peek(rate_action, env_var)
    if used >= cap:
        raise RateLimitExceededError(
            f"Daily rate cap for '{rate_action}' already reached ({used}/{cap}, set via {env_var}); "
            f"refusing further '{action}' actions requested by {agent_name}."
        )
