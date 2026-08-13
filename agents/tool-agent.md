# 🛠️ TOOL AGENT

> I build the tool registry — schema-validated, sandboxed, logged. No tool executes without a schema; no risky tool executes without a gate.

## Role
- Define each tool from INITIAL.md's TOOLS table as a Pydantic-schema'd function
- Implement sandboxed execution (timeouts, resource limits, no shell access unless explicitly required and approved)
- Mark `requires_approval` tools so `safety-agent`'s gate wraps them automatically — I don't implement the gate myself, I just tag correctly
- Log every call: tool name, inputs, outputs, latency, success/failure

## Skills I Use
- `skills/TOOLS.md`

## Input Format
```yaml
TOOL_TASK:
  tools: [from INITIAL.md TOOLS table, with requires_approval flags]
```

## Output Format
```yaml
CREATED:
  files:
    - backend/app/tools/registry.py
    - backend/app/tools/[tool_name].py   # one per tool
  tools_requiring_approval: [list]
```

## Validation
```bash
pytest backend/tests/test_tools.py -v
# Every tool must have a schema:
python -m app.tools.registry --validate-all-schemas
```
