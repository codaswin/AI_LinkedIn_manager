"""Deliberate compaction — the ONLY way a component may shrink to fit budget.

CLAUDE.md non-negotiable #4 / Forbidden section: silently exceeding the
token budget and truncating mid-response is a defect, not a fallback; any
size reduction must go through `compact()` below. No other function in this
package is permitted to slice, cut, or otherwise shorten component text.

A real LLM-based summarizer isn't required yet — same pattern
harness/loop.py uses for its LLM calls: the summarizer is an injectable
callable (`set_summarizer`) that llmops-agent will later wire to
`model_router.route_and_call_sync(task_type="summarization", ...)`. Until
then `_stub_summarizer` produces a clearly-labeled extractive summary so
callers and tests can distinguish "compacted" content from raw content.
"""

from __future__ import annotations

from typing import Callable

from app.context.budget import count_tokens

SummarizerFn = Callable[[str, int], str]


_MARKER = "[compacted]"
_SEPARATOR = " ... "
_OVERHEAD_CHARS = len(_MARKER) + 1 + len(_SEPARATOR)  # marker + space + separator, budgeted explicitly


def _stub_summarizer(text: str, target_tokens: int) -> str:
    if target_tokens <= 0:
        return "[compacted: no budget remained for this section]"

    budget_chars = max(target_tokens * 4, _OVERHEAD_CHARS + 10)
    if len(text) <= budget_chars:
        return text

    # Extractive placeholder (head + tail of the original) sized so the
    # *whole* result, overhead included, stays within budget_chars -- a real
    # summarizer plugged in via set_summarizer() would produce actual prose
    # here instead, but must honor the same target_tokens contract.
    body_chars = max(budget_chars - _OVERHEAD_CHARS, 10)
    head_len = body_chars // 2
    tail_len = body_chars - head_len
    head = text[:head_len].rstrip()
    tail = text[-tail_len:].lstrip()
    return f"{_MARKER} {head}{_SEPARATOR}{tail}"


_summarizer: SummarizerFn = _stub_summarizer


def set_summarizer(fn: SummarizerFn) -> None:
    """Swap in a real (llmops-routed) summarizer without changing any call site."""
    global _summarizer
    _summarizer = fn


def compact(text: str, target_tokens: int) -> str:
    """Summarize `text` down to roughly `target_tokens`.

    This is the single choke point for context size reduction — assembler.py
    calls this instead of ever slicing text itself. If `text` already fits,
    it is returned unchanged (no-op, not a "compaction" event).
    """
    if not text:
        return text
    if count_tokens(text) <= target_tokens:
        return text
    return _summarizer(text, target_tokens)
