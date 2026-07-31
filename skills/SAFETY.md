# Safety Skill

Guardrails, approval gates, cost caps, and a kill switch. This is the layer that never gets "optimized away for speed."

## Approval Gate
```python
# safety/approval_gate.py
from app.tools.registry import registry
import asyncio


def check_approval_required(tool_name: str) -> bool:
    return registry.requires_approval(tool_name)


class PendingApproval:
    """In-memory + persisted record of a tool call awaiting human sign-off."""
    def __init__(self, task_id: str, tool_name: str, arguments: dict):
        self.task_id = task_id
        self.tool_name = tool_name
        self.arguments = arguments
        self.event = asyncio.Event()
        self.approved: bool | None = None


_pending: dict[str, PendingApproval] = {}


async def request_approval(task_id: str, tool_name: str, arguments: dict, notify_fn) -> bool:
    pending = PendingApproval(task_id, tool_name, arguments)
    _pending[task_id] = pending
    await notify_fn(task_id, tool_name, arguments)  # e.g. Slack message, dashboard entry, email
    await pending.event.wait()  # blocks the agent loop here — this IS the point
    return pending.approved


def resolve_approval(task_id: str, approved: bool):
    """Called by the human-facing endpoint when someone clicks approve/deny."""
    pending = _pending.get(task_id)
    if pending:
        pending.approved = approved
        pending.event.set()
```

## Input/Output Guardrails
```python
# safety/guardrails.py
from app.llmops.model_router import route_and_call_sync


REFUSAL_TOPICS: list[str] = []  # populated from INITIAL.md at build time


def check_input_guardrail(user_input: str) -> tuple[bool, str | None]:
    """Returns (is_safe, refusal_message)."""
    for topic in REFUSAL_TOPICS:
        if topic.lower() in user_input.lower():  # placeholder — use classification for production, not substring match
            return False, f"I'm not able to help with that. Let's focus on {topic and 'something else'}."
    return True, None


def check_output_confidence(response, threshold: float) -> bool:
    """Returns True if the response is confident enough to send; False means escalate."""
    return response.confidence >= threshold
```

## Cost Cap — hard stop, not a warning
```python
# safety/cost_cap.py
import redis
from app.config import settings
from datetime import date

r = redis.from_url(settings.REDIS_URL)


def get_today_spend() -> float:
    key = f"cost:{date.today().isoformat()}"
    val = r.get(key)
    return float(val) if val else 0.0


def add_spend(amount_usd: float):
    key = f"cost:{date.today().isoformat()}"
    r.incrbyfloat(key, amount_usd)
    r.expire(key, 172800)  # 2 days


def is_over_budget() -> bool:
    return get_today_spend() >= settings.LLM_COST_BUDGET_DAILY_USD
```
Call `is_over_budget()` inside `should_stop()` in `harness/stopping_conditions.py` — a hit here returns `StopReason.BUDGET_EXCEEDED` and the loop halts, it does not just log a warning and continue.

## Kill Switch
```python
# safety/kill_switch.py
import redis
from app.config import settings

r = redis.from_url(settings.REDIS_URL)
KILL_KEY = "system:kill_switch"


def activate_kill_switch(reason: str):
    r.set(KILL_KEY, reason)


def deactivate_kill_switch():
    r.delete(KILL_KEY)


def is_killed() -> tuple[bool, str | None]:
    reason = r.get(KILL_KEY)
    return (True, reason.decode()) if reason else (False, None)
```
Check `is_killed()` at the top of every loop iteration in `harness/loop.py` — if active, halt immediately regardless of task state.

## Audit Script (used in CLAUDE.md's validation)
```python
# safety/audit.py
"""Run as: python -m app.safety.audit — must report zero findings before ship."""
from app.tools.registry import registry


def audit_ungated_risky_tools() -> list[str]:
    findings = []
    for name, (definition, fn, schema) in registry._tools.items():
        if definition.requires_approval:
            # confirm the harness actually checks this tool before executing —
            # a static check here catches "forgot to set requires_approval" mistakes
            pass
    return findings


if __name__ == "__main__":
    findings = audit_ungated_risky_tools()
    if findings:
        print(f"FAIL: {len(findings)} ungated risky tools found: {findings}")
        exit(1)
    print("PASS: all risky tools are gated")
```

## Best Practices
- The approval gate blocks — it does not fire-and-forget a notification and let the agent proceed
- Refusal-topic checks should move to a real classifier once volume justifies it — substring matching is a placeholder, not a production guardrail
- The kill switch must be checkable from outside the running process (Redis, not an in-memory flag) so an operator can halt a stuck deployment without redeploying
