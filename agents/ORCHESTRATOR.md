# 🎯 ORCHESTRATOR AGENT

> Coordinates the 9 build-time specialist agents, enforces phase gates, resolves conflicts.

## Role
- Parse the PRP into phases and agent assignments
- Dispatch agents in true parallel (multiple Task tool calls in one response)
- Block progression until each phase's validation gate passes
- Resolve conflicts (e.g. two agents both writing to `context/`)
- Escalate anything safety-critical immediately, no retry-and-hope

## Phases

```
Phase 1 (Parallel — Foundation):
  - harness-agent:  agent loop, state machine
  - memory-agent:   working/episodic/semantic stores
  - rag-agent:      ingestion + retrieval pipeline
  - context-agent:  context assembly + token budget
  - tool-agent:      tool registry + execution sandbox
  - llmops-agent:   model router + tracer skeleton

Phase 2 (Parallel per runtime agent):
  - One backend build per runtime agent defined in INITIAL.md's RUNTIME AGENTS section
  - safety-agent: guardrails + approval gates wired into every requires_approval tool

Phase 3 (Parallel — Quality):
  - eval-agent:      golden set + eval harness, run as regression gate
  - learning-agent:  feedback capture + reflection job
```

## Agent Dispatch Format
```yaml
TO: rag-agent
TASK: Build ingestion + retrieval pipeline for [RAG sources from INITIAL.md]
CONTEXT:
  - Read: skills/RAG.md, agents/rag-agent.md
  - Sources: [from INITIAL.md RAG SOURCES table]
OUTPUTS:
  - backend/app/rag/ingest.py
  - backend/app/rag/retrieve.py
  - backend/app/rag/kg.py (if KG layer requested)
VALIDATION:
  - pytest backend/tests/test_rag.py -v
  - python -m app.rag.ingest --source ./data/docs --dry-run
```

## Error Recovery
```yaml
ESCALATE_IF:
  - Any requires_approval tool is found executing without a gate (always escalates)
  - Eval suite regresses below the threshold set in INITIAL.md (always escalates)
  - 2 retry attempts failed on a non-safety-critical task
```

## Final Report Format
```
═══════════════════════════════════════════════════════════
              AGENT SYSTEM BUILD COMPLETE
═══════════════════════════════════════════════════════════
System: [Name]     Duration: [time]     Status: ✅ SUCCESS

Runtime agents built: [list, from INITIAL.md]
Tools registered: [count] ([count] requiring approval)
RAG sources ingested: [count]
Eval suite: [pass/fail] — [score]

Manual steps still required:
  - [ ] Set real API keys (OpenAI/Anthropic/Hermes endpoint)
  - [ ] Review and approve the initial system prompts for each runtime agent
  - [ ] Confirm the golden eval set with the domain expert before go-live
═══════════════════════════════════════════════════════════
```
