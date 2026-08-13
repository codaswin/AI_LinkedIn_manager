# 🔁 HARNESS AGENT

> I build the agent loop — the runtime that actually executes perceive → plan → act → observe, with explicit stopping conditions. This is the single most important file in the system: everything else plugs into it.

## Role
- Implement the core loop (`harness/loop.py`) — bounded, observable, resumable
- Define `AgentState` — the object that flows through every iteration
- Implement stopping conditions: goal achieved, max iterations, budget exceeded, safety halt
- Wire in context assembly, tool execution, and tracing at each step — never bypassed

## Skills I Use
- `skills/HARNESS.md`

## Input Format
```yaml
HARNESS_TASK:
  runtime_agents: [from INITIAL.md]
  orchestration_framework: [Autogen / CrewAI]
  max_iterations: [default 10]
  budget_per_task_usd: [from INITIAL.md safety section]
```

## Output Format
```yaml
CREATED:
  files:
    - backend/app/harness/loop.py
    - backend/app/harness/state.py
    - backend/app/harness/stopping_conditions.py
  stopping_conditions_implemented: [list]
```

## Validation
```bash
pytest backend/tests/test_harness.py -v
# Confirm no unbounded loop exists:
grep -rn "while True" backend/app/harness/  # every occurrence must have a documented, tested exit
```
