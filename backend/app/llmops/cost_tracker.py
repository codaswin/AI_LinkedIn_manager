"""Daily LLM spend tracking with a hard stop at LLM_COST_BUDGET_DAILY_USD.

Storage choice: in-memory, process-local dict keyed by UTC calendar date,
guarded by a threading.Lock. Chosen over a Postgres/Redis-backed store
because this module ships in the llmops foundation phase, before
memory-agent's persistent stores exist, and the harness only needs
same-process visibility to enforce the cap within a single running service.
The public functions below (check_budget / record_cost) are the seam a
later phase can swap to a durable backend (e.g. the `agent_settings`-style
Postgres table) without changing any caller.
"""

from __future__ import annotations

import os
import threading
from collections import defaultdict
from datetime import datetime, timezone

_DEFAULT_DAILY_BUDGET_USD = 10.0

_lock = threading.Lock()
_spend_by_day: dict[str, float] = defaultdict(float)


class CostBudgetExceededError(RuntimeError):
    """Raised to hard-stop further LLM calls once the daily budget is reached.

    WHY this hard-stops instead of logging a warning: CLAUDE.md's Forbidden
    list explicitly calls out "cost caps implemented as warnings instead of
    hard stops" as a defect that must escalate immediately. A cap that only
    logs still lets the system spend past the configured daily limit; raising
    here is what makes the harness's run_step() actually refuse the call
    rather than merely noting that it should have.
    """

    def __init__(self, spent_usd: float, budget_usd: float) -> None:
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        super().__init__(
            f"Daily LLM cost budget exceeded: spent ${spent_usd:.4f} of ${budget_usd:.2f} cap. "
            "Refusing further LLM calls until the daily window resets."
        )


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _budget_usd() -> float:
    raw = os.environ.get("LLM_COST_BUDGET_DAILY_USD")
    if raw is None or raw == "":
        return _DEFAULT_DAILY_BUDGET_USD
    return float(raw)


def get_today_spend() -> float:
    """Total recorded spend (USD) for the current UTC day."""
    with _lock:
        return _spend_by_day[_today_key()]


def get_cost_summary() -> dict[str, float]:
    return {"today_usd": get_today_spend(), "budget_usd": _budget_usd()}


def check_budget() -> None:
    """Raise CostBudgetExceededError if today's spend has already reached the cap.

    Call this BEFORE making an LLM call. Cost tracking checks the budget
    before the call, not after (skills/LLMOPS.md § Best Practices) — that is
    what turns the cap into a real block instead of a post-hoc log line.
    """
    budget = _budget_usd()
    spent = get_today_spend()
    if spent >= budget:
        raise CostBudgetExceededError(spent_usd=spent, budget_usd=budget)


def record_cost(amount_usd: float) -> None:
    """Record actual spend for a completed LLM call.

    Does not itself block — callers must call check_budget() before the call
    this cost is paying for. Negative amounts are rejected since they would
    let a caller claw back budget headroom that was never actually reserved.
    """
    if amount_usd < 0:
        raise ValueError(f"amount_usd must be >= 0, got {amount_usd}")
    with _lock:
        _spend_by_day[_today_key()] += amount_usd


def reset_for_testing() -> None:
    """Clear all recorded spend. Test-only — production code must never call this."""
    with _lock:
        _spend_by_day.clear()
