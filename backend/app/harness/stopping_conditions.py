"""Explicit stopping conditions for a bounded agent run.

skills/HARNESS.md Best Practices: budget checks happen before the LLM call is
made, not after — you can't un-spend it. should_stop() is called at the top
of every loop iteration in loop.run_agent(), before run_step() (and therefore
before any LLM call) executes.

Defaults live on AgentState (state.DEFAULT_MAX_ITERATIONS,
state.DEFAULT_BUDGET_USD) so there is one source of truth; they are re-
exported here for callers that only import stopping_conditions.
"""

from __future__ import annotations

from app.harness.state import (
    DEFAULT_BUDGET_USD,
    DEFAULT_MAX_ITERATIONS,
    AgentState,
    StopReason,
)

__all__ = [
    "DEFAULT_BUDGET_USD",
    "DEFAULT_MAX_ITERATIONS",
    "should_stop",
]


def should_stop(state: AgentState) -> StopReason | None:
    """Return the reason to stop, or None if the run may continue.

    Iteration cap is checked before budget so a run that has already hit
    max_iterations reports MAX_ITERATIONS even if it also happens to be over
    budget — the two are independent halts, but iteration is the more common
    / expected one and callers may branch on the specific reason.
    """
    if state.iteration >= state.max_iterations:
        return StopReason.MAX_ITERATIONS
    if state.cost_so_far_usd >= state.budget_usd:
        return StopReason.BUDGET_EXCEEDED
    return None
