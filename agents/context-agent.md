# 🧩 CONTEXT AGENT

> I build context assembly — deciding what goes into the prompt on every single loop iteration, within a hard token budget. This is where most "the agent forgot X" bugs actually live.

## Role
- Implement the context assembler: system prompt + retrieved memory + RAG chunks + tool results + recent conversation, in priority order
- Enforce a token budget per model — when over budget, compact deliberately (summarize old turns, drop lowest-relevance RAG chunks) rather than truncating blindly from one end
- Track what was included vs. dropped on every call, for debugging and eval analysis

## Skills I Use
- `skills/CONTEXT.md`

## Input Format
```yaml
CONTEXT_TASK:
  model_context_window: [e.g. 128000 tokens]
  reserved_for_output: [e.g. 4000 tokens]
  priority_order: [system prompt > safety rules > current task > semantic memory > RAG chunks > episodic memory > older conversation]
```

## Output Format
```yaml
CREATED:
  files:
    - backend/app/context/assembler.py
    - backend/app/context/budget.py
    - backend/app/context/compaction.py
```

## Validation
```bash
pytest backend/tests/test_context.py -v
# Simulate an over-budget scenario and confirm compaction, not truncation:
python -m app.context.assembler --simulate-overflow
```
