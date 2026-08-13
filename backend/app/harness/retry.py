"""Retry policy per agents/ORCHESTRATOR.md's Error Recovery section.

  ESCALATE_IF:
    - Any requires_approval tool is found executing without a gate (always escalates)
    - Eval suite regresses below the threshold set in INITIAL.md (always escalates)
    - 2 retry attempts failed on a non-safety-critical task

Non-safety-critical failures (LLM API hiccups, tool timeouts, transient
network errors) get up to 2 retry attempts with backoff. Safety-critical
failures must NEVER be auto-retried: retrying could mean re-attempting the
unsafe action itself (e.g. re-issuing a publish that just executed without
its approval gate), so the only correct move is to propagate the failure
immediately so it reaches a human/escalation path, not another attempt.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class SafetyCriticalError(Exception):
    """Base class for failures that must escalate immediately, never retry."""


class UngatedApprovalToolError(SafetyCriticalError):
    """A requires_approval tool executed (or was about to) without an approval gate."""


class MissingGuardrailError(SafetyCriticalError):
    """A refusal-topic / guardrail check that should have run did not run or is absent."""


class CostCapFailOpenError(SafetyCriticalError):
    """A cost or rate cap failed open (would allow spend/action past the configured cap)."""


def _default_is_safety_critical(exc: BaseException) -> bool:
    return isinstance(exc, SafetyCriticalError)


async def with_retry(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 2,
    base_delay: float = 1.0,
    is_safety_critical: Callable[[BaseException], bool] | None = None,
    **kwargs: Any,
) -> T:
    """Await fn(*args, **kwargs), retrying up to max_retries times on failure.

    max_retries=2 means up to 2 retries AFTER the initial attempt (3 attempts
    total), matching ORCHESTRATOR.md's "2 retry attempts failed" language.
    Any exception classified as safety-critical (default: isinstance check
    against SafetyCriticalError, override via is_safety_critical for
    third-party exception types the safety layer wants to treat the same way)
    is re-raised on the very first occurrence — no retry, no backoff.
    """
    classify = is_safety_critical or _default_is_safety_critical
    attempt = 0
    while True:
        try:
            return await fn(*args, **kwargs)
        except BaseException as exc:
            if classify(exc):
                raise
            attempt += 1
            if attempt > max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            await asyncio.sleep(delay)
