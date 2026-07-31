# Generate PRP Command

You are a **Product PRP Generator** for a production-grade agent system.

## Input
Read the product definition file: $ARGUMENTS (defaults to INITIAL.md)

## Context Files
Read before generating: `CLAUDE.md`, `agents/ORCHESTRATOR.md`

---

## STEP 1: Parse INITIAL.md

```yaml
SYSTEM: {name, purpose, type}
TECH_STACK: {orchestration, inference_primary, inference_worker, rag, serving, memory_store}
RUNTIME_AGENTS: [{name, goal, inputs, outputs, model_tier}]
TOOLS: [{name, purpose, requires_approval}]
MEMORY: {working, episodic, semantic, retention}
RAG_SOURCES: [{source, type, update_frequency, chunking_strategy}]
KG_REQUIRED: bool
SAFETY: {approval_actions, confidence_threshold, refusal_topics, cost_cap}
EVAL: {golden_set_source, metrics, regression_threshold}
LEARNING: {feedback_signals, auto_apply_scope, human_review_scope}
MVP_SCOPE, ACCEPTANCE_CRITERIA, FORBIDDEN
```

If any section is missing or still has placeholder brackets — especially SAFETY or EVAL — stop and ask the user to complete it. These two sections are not optional for a production-grade system.

## STEP 2: Map to build-time agents

```yaml
HARNESS-AGENT:  runtime agents' loop structure, stopping conditions, budget
MEMORY-AGENT:   MEMORY section
RAG-AGENT:      RAG_SOURCES, KG_REQUIRED
CONTEXT-AGENT:  token budget for the chosen model(s)
TOOL-AGENT:     TOOLS
SAFETY-AGENT:   SAFETY, TOOLS' requires_approval flags
EVAL-AGENT:     EVAL
LLMOPS-AGENT:   TECH_STACK inference choices
LEARNING-AGENT: LEARNING
```

## STEP 3: Generate the PRP file

Create `PRPs/[system-name-kebab-case]-prp.md`:

```markdown
# PRP: [System Name]

## METADATA
| Field | Value |
|-------|-------|
| System | [Name] |
| Type | [Single-agent / Multi-agent / Workflow] |
| Complexity | [based on # runtime agents + tools] |

## SYSTEM OVERVIEW
[Purpose, MVP scope as checklist]

## TECH STACK
[Table: layer -> technology -> skill reference]

## RUNTIME AGENTS
[For each: goal, inputs/outputs, model tier, tools it can call, escalation condition]

## TOOLS
[Table: tool -> purpose -> requires_approval]

## MEMORY ARCHITECTURE
[Working / episodic / semantic scope + retention, from INITIAL.md]

## RAG PIPELINE
[Sources table + chunking strategy + KG layer yes/no]

## SAFETY REQUIREMENTS
[Approval-gated actions, confidence threshold, refusal topics, cost cap]

## EVALUATION PLAN
[Golden set source, metrics, regression threshold]

## SELF-LEARNING SCOPE
[Auto-apply vs. human-review change types]

## PHASE EXECUTION PLAN
Phase 1 (Parallel — Foundation): harness-agent, memory-agent, rag-agent, context-agent, tool-agent, llmops-agent
Phase 2 (Parallel per runtime agent + safety-agent wiring approval gates)
Phase 3 (Parallel — Quality): eval-agent, learning-agent

## VALIDATION GATES
| Gate | Commands |
|------|----------|
| 1 | pytest test_harness.py, test_memory.py, test_rag.py; safety audit script |
| 2 | pytest test_tools.py, test_safety.py; grep for ungated requires_approval tools |
| 3 | pytest evals -v --tb=short; compare-to-baseline |
| Final | full validation suite from CLAUDE.md, load test within cost budget |

## ENVIRONMENT VARIABLES
[From CLAUDE.md, plus any project-specific ones]

## NEXT STEP
/execute-prp PRPs/[system-name]-prp.md
```

## Output
Save to `PRPs/[system-name-kebab-case]-prp.md`. Report runtime agent count, tool count (and how many require approval), RAG source count, and the 3-phase plan.
