"""Agent loop / runtime harness.

run_step() in loop.py is the sole choke point for LLM calls in this
codebase (project invariant #1). Nothing else here imports or
calls an LLM client.
"""

from app.harness.loop import (
    AgentRunConfig,
    LLMResponse,
    ToolCallRequest,
    ToolResult,
    run_agent,
    run_step,
)
from app.harness.retry import (
    CostCapFailOpenError,
    MissingGuardrailError,
    SafetyCriticalError,
    UngatedApprovalToolError,
    with_retry,
)
from app.harness.state import (
    AgentState,
    ApprovalStatus,
    RuntimeAgentName,
    StopReason,
    ToolCallRecord,
)
from app.harness.stopping_conditions import should_stop

__all__ = [
    "AgentRunConfig",
    "AgentState",
    "ApprovalStatus",
    "CostCapFailOpenError",
    "LLMResponse",
    "MissingGuardrailError",
    "RuntimeAgentName",
    "SafetyCriticalError",
    "StopReason",
    "ToolCallRecord",
    "ToolCallRequest",
    "ToolResult",
    "UngatedApprovalToolError",
    "run_agent",
    "run_step",
    "should_stop",
    "with_retry",
]
