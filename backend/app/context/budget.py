"""Token budget primitives, generic across model tiers.

This module owns budgeting *logic* only — it never hardcodes a specific
model's context window (CLAUDE.md forbids hardcoded model names/config
outside `llmops/model_router.py`). llmops-agent's model_router is expected
to call `register_tier_budget()` once per tier it routes to (e.g. "primary"
for the hosted Claude tier, "worker" for the self-hosted Hermes tier) with
the real numbers; everything downstream (assembler.py) only ever consumes
the resulting integer budget via `get_budget()` or a directly-supplied int.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TIER_BUDGETS: dict[str, int] = {}


def register_tier_budget(tier: str, budget_tokens: int) -> None:
    """Called by llmops/model_router.py to publish a tier's usable token budget.

    `budget_tokens` should already account for the model's context window
    minus whatever model_router reserves for output — that arithmetic is
    llmops's call, not this module's.
    """
    if budget_tokens <= 0:
        raise ValueError(f"budget_tokens must be positive, got {budget_tokens}")
    TIER_BUDGETS[tier] = budget_tokens


def get_budget(tier: str) -> int:
    try:
        return TIER_BUDGETS[tier]
    except KeyError as exc:
        raise KeyError(
            f"No token budget registered for tier {tier!r}. "
            "llmops/model_router.py must call register_tier_budget(tier, tokens) "
            "for every tier it routes to before context assembly can run."
        ) from exc


def count_tokens(text: str) -> int:
    """Cheap, dependency-free token estimate (~4 chars/token).

    Swappable for a real tokenizer at the call site later; the rest of this
    package only depends on "some monotonic token-count function", not on
    any particular tokenizer implementation.
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


@dataclass
class TokenBudget:
    """Tracks how much of a single integer budget has been spent so far."""

    total_tokens: int
    used_tokens: int = 0
    components: dict[str, int] = field(default_factory=dict)

    def remaining(self) -> int:
        return self.total_tokens - self.used_tokens

    def can_fit(self, text: str) -> bool:
        return count_tokens(text) <= self.remaining()

    def reserve(self, label: str, text: str) -> bool:
        """Reserve space for `text` if it fits in what remains. Returns False, does not mutate, if it doesn't."""
        tokens = count_tokens(text)
        if tokens > self.remaining():
            return False
        self.used_tokens += tokens
        self.components[label] = tokens
        return True

    def force_reserve(self, label: str, text: str) -> None:
        """Reserve space unconditionally, even past `remaining()`.

        Reserved for the tiers CLAUDE.md says must never be compacted or
        dropped (system prompt, safety rules). If they alone exceed the
        budget, that is a config/escalation problem upstream — this method
        will not paper over it by truncating them; `remaining()` simply
        goes negative and every lower-priority tier compacts harder.
        """
        tokens = count_tokens(text)
        self.used_tokens += tokens
        self.components[label] = tokens
