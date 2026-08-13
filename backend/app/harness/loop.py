"""The agent loop: perceive -> plan -> act -> observe, with explicit stops.

CHOKE POINT CONTRACT (project invariant #1):

    run_step() is the ONLY function in this entire codebase permitted to
    call out to an LLM. Every runtime agent (Strategist, Writer, Engagement,
    Analytics & Reporting, Research) must go through run_step() for every
    model call it makes. No tool, no router, no runtime-agent module may
    hold a reference to an LLM client and invoke it directly.

run_step() does not talk to a real model yet. The `llm_client` argument is
an injectable async callable — llmops-agent's model_router.route_and_call
will be wired in here later without this file's contract changing. The
point of this module is the CHOKE POINT CONTRACT, not a working model
integration.

Tool execution is similarly injected via `tool_executor`. That keeps
approval-gating (project invariant #3: requires_approval tools block until a
human approves, no exceptions) out of harness's scope — that's
the safety module's domain, built on top of tools.registry.execute_tool. Because
run_step *awaits* tool_executor, a real implementation that blocks pending
human approval will naturally block this loop too; harness only guarantees
the call happens, is timed, and is logged.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from app.harness.state import (
    AgentState,
    ApprovalStatus,
    StopReason,
    ToolCallRecord,
)
from app.harness.stopping_conditions import should_stop


@dataclass
class ToolCallRequest:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    outputs: Any
    cost_usd: float = 0.0


class LLMResponse(Protocol):
    text: str
    tool_calls: list[ToolCallRequest]
    cost_usd: float
    confidence: float | None
    goal_achieved: bool


# Injected by llmops.model_router.route_and_call — not imported here on
# purpose, so harness/ has zero import-time dependency on any LLM SDK.
LLMClient = Callable[..., Awaitable[LLMResponse]]

# Injected by tools.registry.execute_tool (wrapped with the safety module's
# approval gate before it ever reaches here).
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[ToolResult]]

EscalationCondition = Callable[[AgentState], bool]


@dataclass
class AgentRunConfig:
    """Per-agent config that runtime-agent code plugs into run_agent()."""

    agent_name: str
    allowed_tools: list[str]
    model_tier: str  # e.g. "primary" (Claude) or "worker" (Hermes) — resolved by model_router, never hardcoded here
    escalation_condition: EscalationCondition | None = None
    task_type: str = "default"


async def run_step(
    state: AgentState,
    config: AgentRunConfig,
    llm_client: LLMClient,
    tool_executor: ToolExecutor | None = None,
) -> AgentState:
    """Run exactly one perceive -> plan -> act -> observe iteration.

    This is the SINGLE choke point for LLM calls — see module docstring.
    Every tool call's log entry (inputs, outputs, latency, cost) is appended
    to state.tool_calls before this function returns, i.e. before control
    goes back to the caller's loop for the next iteration (project invariant #2).
    """
    llm_start = time.perf_counter()
    response = await llm_client(state=state, config=config)
    llm_latency_ms = (time.perf_counter() - llm_start) * 1000

    state.conversation.append(
        {
            "role": "assistant",
            "content": response.text,
            "latency_ms": llm_latency_ms,
        }
    )
    state.cost_so_far_usd += response.cost_usd

    if response.confidence is not None:
        state.confidence = response.confidence

    for call in response.tool_calls or []:
        if call.tool_name not in config.allowed_tools:
            raise PermissionError(
                f"agent '{config.agent_name}' attempted to call "
                f"disallowed tool '{call.tool_name}'"
            )
        if tool_executor is None:
            raise RuntimeError(
                f"agent '{config.agent_name}' requested tool "
                f"'{call.tool_name}' but no tool_executor was provided"
            )

        tool_start = time.perf_counter()
        result = await tool_executor(call.tool_name, call.arguments)
        latency_ms = (time.perf_counter() - tool_start) * 1000

        state.tool_calls.append(
            ToolCallRecord(
                tool_name=call.tool_name,
                inputs=call.arguments,
                outputs=result.outputs,
                latency_ms=latency_ms,
                cost_usd=result.cost_usd,
                timestamp=datetime.now(timezone.utc),
            )
        )
        state.cost_so_far_usd += result.cost_usd

    state.iteration += 1

    if config.escalation_condition is not None and config.escalation_condition(state):
        state.approval_status = ApprovalStatus.PENDING
        state.stop_reason = StopReason.ESCALATED
        return state

    if response.goal_achieved:
        state.stop_reason = StopReason.GOAL_ACHIEVED

    return state


async def run_agent(
    state: AgentState,
    config: AgentRunConfig,
    llm_client: LLMClient,
    tool_executor: ToolExecutor | None = None,
) -> AgentState:
    """Bounded outer loop: check stopping conditions, then run_step().

    should_stop() runs before run_step() every iteration (skills/HARNESS.md:
    budget checks must happen before the LLM call, since you can't un-spend
    it once made) — this is the one `while True` in the module, and it is
    bounded by should_stop() on every pass, reachable within
    state.max_iterations iterations by construction.
    """
    while True:
        stop = should_stop(state)
        if stop is not None:
            state.stop_reason = stop
            break

        state = await run_step(state, config, llm_client, tool_executor)

        if state.stop_reason is not None:
            break

    return state
