"""AgentState — the single object that flows through every harness iteration.

One AgentState per task, never shared mutable state across concurrent tasks
(skills/HARNESS.md Best Practices). It is a Pydantic model rather than a plain
dataclass so it validates on construction and serializes cleanly through
FastAPI later (e.g. for resuming a crashed task from its last iteration).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RuntimeAgentName(str, Enum):
    """The 5 runtime agents defined in the PRP's RUNTIME AGENTS section."""

    CONTENT_STRATEGIST = "content_strategist"
    CONTENT_WRITER = "content_writer"
    ENGAGEMENT = "engagement"
    ANALYTICS_REPORTING = "analytics"  # matches model_router._ROUTING_TABLE's ("analytics", ...) keys
    RESEARCH = "research"


class StopReason(str, Enum):
    GOAL_ACHIEVED = "goal_achieved"
    MAX_ITERATIONS = "max_iterations"
    BUDGET_EXCEEDED = "budget_exceeded"
    SAFETY_HALT = "safety_halt"
    ESCALATED = "escalated"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NOT_REQUIRED = "not_required"


class ToolCallRecord(BaseModel):
    """One logged tool call. CLAUDE.md rule #2: inputs/outputs/latency/cost

    must be captured for every tool call before the loop continues — this is
    the record shape that requirement is enforced against.
    """

    tool_name: str
    inputs: dict[str, Any]
    outputs: Any
    latency_ms: float
    cost_usd: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Judgment call (PRP doesn't specify a per-run iteration cap): 10 gives a
# plan -> act -> observe cycle enough room to finish multi-tool tasks (e.g.
# Engagement Agent triaging several notifications) without letting a stuck
# agent spin indefinitely before the budget check would otherwise catch it.
DEFAULT_MAX_ITERATIONS = 10

# PRP SAFETY REQUIREMENTS: "$10/day LLM spend" is the system-wide cost
# ceiling. Used here as the default per-run budget context until per-agent /
# per-task sub-budgets are introduced; actual system-wide daily aggregation
# is llmops' responsibility (cost tracking toward the $10/day cap), not this
# per-run field.
DEFAULT_BUDGET_USD = 10.0


class AgentState(BaseModel):
    task_id: str
    agent_name: RuntimeAgentName
    current_task: str

    iteration: int = 0
    max_iterations: int = DEFAULT_MAX_ITERATIONS

    cost_so_far_usd: float = 0.0
    budget_usd: float = DEFAULT_BUDGET_USD

    conversation: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    scratchpad: dict[str, Any] = Field(default_factory=dict)

    # Nullable: only Content Writer, Engagement, and Research populate this
    # (PRP escalation conditions keyed on confidence < 0.75). Content
    # Strategist and Analytics & Reporting leave it None — they don't have a
    # confidence-based escalation condition.
    confidence: float | None = None

    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    stop_reason: StopReason | None = None

    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
