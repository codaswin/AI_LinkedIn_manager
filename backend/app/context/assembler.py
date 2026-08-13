"""Priority-ordered context assembly within a hard token budget.

Priority order, highest first (context assembly requirements):

    system_prompt > safety_rules > current_task > semantic_memory >
    rag_context > episodic_memory > older_conversation

Higher-priority components are filled first and compacted last, if ever.
system_prompt and safety_rules never compact or drop, no matter how far
over budget the rest of the context is (project rules rule #4: this is the one
thing that must never yield). Everything else that doesn't fit is handed to
compaction.compact() — there is no code path in this module that drops or
truncates a component outright.
"""

from __future__ import annotations

from typing import TypedDict

from app.context.budget import TokenBudget, count_tokens
from app.context.compaction import compact


class ContextBlock(TypedDict):
    label: str
    content: str
    compacted: bool


MANDATORY_ORDER: tuple[str, ...] = ("system_prompt", "safety_rules")

COMPACTABLE_ORDER: tuple[str, ...] = (
    "current_task",
    "semantic_memory",
    "rag_context",
    "episodic_memory",
    "older_conversation",
)

PRIORITY_ORDER: tuple[str, ...] = MANDATORY_ORDER + COMPACTABLE_ORDER


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return "" if value is None else str(value)


def assemble_context(components: dict[str, object], budget: int) -> list[ContextBlock]:
    """Assemble one agent turn's context, respecting `budget` tokens.

    `components` maps priority-tier labels (any subset of PRIORITY_ORDER) to
    text or a list of text chunks. Returns the included blocks in priority
    order, each flagged with whether it was compacted to fit.

    Over-budget handling compacts from the LOWEST priority present component
    upward (older_conversation, then episodic_memory, then rag_context, then
    semantic_memory, then current_task as a last resort) so the components
    most likely to lose detail are the ones the agent needs least this turn.
    """
    texts = {label: _as_text(components[label]) for label in PRIORITY_ORDER if label in components}

    tb = TokenBudget(total_tokens=budget)
    for label in MANDATORY_ORDER:
        if label in texts:
            tb.force_reserve(label, texts[label])

    present_compactable = [label for label in COMPACTABLE_ORDER if label in texts]
    sizes = {label: count_tokens(texts[label]) for label in present_compactable}
    compacted_flags = {label: False for label in present_compactable}
    available_for_compactable = tb.remaining()

    def total_needed() -> int:
        return sum(sizes.values())

    # Lowest priority first: reversed(present_compactable) walks
    # older_conversation -> episodic_memory -> rag_context -> semantic_memory
    # -> current_task, squeezing each down to whatever's left before ever
    # touching the next tier up.
    for label in reversed(present_compactable):
        if total_needed() <= max(available_for_compactable, 0):
            break
        others_total = total_needed() - sizes[label]
        target = max(available_for_compactable, 0) - others_total
        if sizes[label] > max(target, 0):
            texts[label] = compact(texts[label], max(target, 0))
            sizes[label] = count_tokens(texts[label])
            compacted_flags[label] = True

    for label in present_compactable:
        tb.force_reserve(label, texts[label])

    return [
        {"label": label, "content": texts[label], "compacted": compacted_flags.get(label, False)}
        for label in PRIORITY_ORDER
        if label in texts
    ]
