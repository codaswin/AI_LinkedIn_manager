"""Structured tracer: logs inputs, outputs, latency_ms, and cost_usd per LLM call.

Sink is pluggable via the TRACE_SINK env var (default "local"). Only "local"
(structured logs via structlog, per CLAUDE.md's tech stack) is implemented in
this phase; an unrecognized value falls back to "local" with a warning so a
misconfigured env var never silently drops trace events. Swapping in a hosted
sink (Langfuse/Phoenix) later means adding a TraceSink subclass and
registering it in _SINKS — trace_llm_call()'s signature stays stable so
nothing else in the codebase changes.
"""

from __future__ import annotations

import abc
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

_logger = structlog.get_logger("llmops.tracer")


@dataclass(frozen=True)
class LLMCallTrace:
    """One traced LLM call — the record every sink implementation must emit."""

    trace_id: str
    agent: str
    step: str
    model: str
    provider: str
    latency_ms: float
    cost_usd: float
    inputs: Any
    outputs: Any
    tokens_in: int | None = None
    tokens_out: int | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TraceSink(abc.ABC):
    """Pluggable trace destination. Sync by design — local-file/log sinks need
    no network I/O; a future hosted sink can still implement this interface
    and do its own async dispatch internally if needed.
    """

    @abc.abstractmethod
    def emit_llm_call(self, trace: LLMCallTrace) -> None: ...


class LocalTraceSink(TraceSink):
    """Default sink: structured logs via structlog."""

    def emit_llm_call(self, trace: LLMCallTrace) -> None:
        _logger.info(
            "llm_call",
            trace_id=trace.trace_id,
            agent=trace.agent,
            step=trace.step,
            model=trace.model,
            provider=trace.provider,
            latency_ms=trace.latency_ms,
            cost_usd=trace.cost_usd,
            tokens_in=trace.tokens_in,
            tokens_out=trace.tokens_out,
            inputs=trace.inputs,
            outputs=trace.outputs,
            timestamp=trace.timestamp,
        )


_SINKS: dict[str, type[TraceSink]] = {
    "local": LocalTraceSink,
}

_sink_instance: TraceSink | None = None


def _get_sink() -> TraceSink:
    global _sink_instance
    if _sink_instance is not None:
        return _sink_instance
    name = os.environ.get("TRACE_SINK", "local")
    sink_cls = _SINKS.get(name)
    if sink_cls is None:
        _logger.warning("trace_sink_unknown_fallback_local", requested_sink=name)
        sink_cls = LocalTraceSink
    _sink_instance = sink_cls()
    return _sink_instance


def trace_llm_call(
    *,
    agent: str,
    step: str,
    model: str,
    provider: str,
    latency_ms: float,
    cost_usd: float,
    inputs: Any,
    outputs: Any,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    trace_id: str | None = None,
) -> str:
    """Log one completed LLM call. Returns the trace_id used (generated if not given).

    Called by the harness's run_step() after a routed call completes — every
    LLM call is logged with inputs, outputs, latency, and cost before the
    loop continues (CLAUDE.md non-negotiable #2).
    """
    resolved_trace_id = trace_id or str(uuid.uuid4())
    trace = LLMCallTrace(
        trace_id=resolved_trace_id,
        agent=agent,
        step=step,
        model=model,
        provider=provider,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        inputs=inputs,
        outputs=outputs,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
    _get_sink().emit_llm_call(trace)
    return resolved_trace_id


def reset_sink_for_testing() -> None:
    """Force the sink to be re-resolved from TRACE_SINK on next call. Test-only."""
    global _sink_instance
    _sink_instance = None
