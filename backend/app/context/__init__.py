"""Context assembly + token budgeting (CLAUDE.md non-negotiable rule #4).

Context is always assembled within a hard per-tier token budget; when it
would overflow, the lowest-priority components are compacted (never
silently dropped or truncated). See assembler.py for the priority order and
compaction.py for the compaction contract.
"""

from app.context.assembler import (
    COMPACTABLE_ORDER,
    MANDATORY_ORDER,
    PRIORITY_ORDER,
    ContextBlock,
    assemble_context,
)
from app.context.budget import (
    TIER_BUDGETS,
    TokenBudget,
    count_tokens,
    get_budget,
    register_tier_budget,
)
from app.context.compaction import compact, set_summarizer

__all__ = [
    "COMPACTABLE_ORDER",
    "MANDATORY_ORDER",
    "PRIORITY_ORDER",
    "TIER_BUDGETS",
    "ContextBlock",
    "TokenBudget",
    "assemble_context",
    "compact",
    "count_tokens",
    "get_budget",
    "register_tier_budget",
    "set_summarizer",
]
