# Harness Skill

The agent loop — perceive → plan → act → observe — with explicit stopping conditions. Everything else in the system plugs into this.

## Agent State
```python
# harness/state.py
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


class StopReason(str, Enum):
    GOAL_ACHIEVED = "goal_achieved"
    MAX_ITERATIONS = "max_iterations"
    BUDGET_EXCEEDED = "budget_exceeded"
    SAFETY_HALT = "safety_halt"
    ESCALATED = "escalated"


@dataclass
class AgentState:
    task_id: str
    goal: str
    iteration: int = 0
    max_iterations: int = 10
    cost_so_far_usd: float = 0.0
    budget_usd: float = 1.0
    conversation: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    scratchpad: dict = field(default_factory=dict)  # working memory for this task
    stop_reason: StopReason | None = None
    started_at: datetime = field(default_factory=datetime.utcnow)
```

## The Loop
```python
# harness/loop.py
from app.harness.state import AgentState, StopReason
from app.harness.stopping_conditions import should_stop
from app.context.assembler import assemble_context
from app.llmops.model_router import route_and_call
from app.tools.registry import execute_tool
from app.llmops.tracer import trace_step
from app.safety.approval_gate import check_approval_required


async def run_agent(state: AgentState, runtime_agent_config: dict) -> AgentState:
    while True:
        stop = should_stop(state)
        if stop:
            state.stop_reason = stop
            break

        # 1. PERCEIVE + PLAN: assemble context, ask the model what to do next
        context = await assemble_context(state, runtime_agent_config)
        response = await route_and_call(
            task_type=runtime_agent_config["task_type"],
            context=context,
            state=state,
        )
        trace_step(state.task_id, "plan", response)

        # 2. ACT: execute any tool calls the model requested
        if response.tool_calls:
            for call in response.tool_calls:
                if check_approval_required(call.tool_name):
                    approved = await wait_for_human_approval(state.task_id, call)
                    if not approved:
                        state.stop_reason = StopReason.ESCALATED
                        return state

                result = await execute_tool(call.tool_name, call.arguments)
                state.tool_calls.append({"call": call, "result": result})
                trace_step(state.task_id, "act", result)

        # 3. OBSERVE: update conversation + cost, check for goal completion
        state.conversation.append({"role": "assistant", "content": response.text})
        state.cost_so_far_usd += response.cost_usd
        state.iteration += 1

        if response.goal_achieved:
            state.stop_reason = StopReason.GOAL_ACHIEVED
            break

    return state


async def wait_for_human_approval(task_id: str, call) -> bool:
    # Implementation depends on channel — webhook callback, polling, or Slack/WhatsApp prompt.
    # Must be a real block, not a fire-and-forget notification.
    ...
```

## Stopping Conditions
```python
# harness/stopping_conditions.py
from app.harness.state import AgentState, StopReason


def should_stop(state: AgentState) -> StopReason | None:
    if state.iteration >= state.max_iterations:
        return StopReason.MAX_ITERATIONS
    if state.cost_so_far_usd >= state.budget_usd:
        return StopReason.BUDGET_EXCEEDED
    return None
```

## Retry / Backoff for transient failures (LLM API errors, tool timeouts)
```python
# harness/retry.py
import asyncio
import random


async def with_retry(fn, *args, max_retries=3, base_delay=1.0, **kwargs):
    for attempt in range(max_retries):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            await asyncio.sleep(delay)
```

## Best Practices
- Every `while True` must have a `should_stop` check reachable within a bounded number of iterations — no exceptions
- Budget checks happen before the LLM call is made, not after (you can't un-spend it)
- The loop is resumable: `AgentState` is serializable, so a crashed task can resume from its last iteration rather than restarting
- One `AgentState` per task, never shared mutable state across concurrent tasks
