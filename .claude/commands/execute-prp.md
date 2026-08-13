# Execute PRP Command

You are the **ORCHESTRATOR**. Execute the PRP by coordinating agents in **TRUE PARALLEL**, with a blocking validation gate between every phase, and zero tolerance for skipping safety/eval work.

## Input
Read the PRP file: $ARGUMENTS

---

## CRITICAL: How Parallel Execution Works

Dispatch multiple Task tool calls in a **single response** per phase:
```
subagent_type: "general-purpose"
run_in_background: true
One Task call per agent, all in the same response
```
Sequential responses = sequential execution.

---

## STEP 1: Read PRP & Load Context

Read `agents/ORCHESTRATOR.md` and the PRP file. Extract runtime agents, tools, memory scope, RAG sources, safety requirements, eval plan.

---

## STEP 2: PHASE 1 — Foundation (dispatch 6 agents in ONE response)

### HARNESS-AGENT
```
READ: agents/harness-agent.md, skills/HARNESS.md
BUILD: harness/state.py, harness/loop.py, harness/stopping_conditions.py, harness/retry.py
Use max_iterations and budget_usd from the PRP's runtime agent specs.
```

### MEMORY-AGENT
```
READ: agents/memory-agent.md, skills/MEMORY.md
BUILD: memory/working.py, memory/episodic.py, memory/semantic.py, memory/policy.py,
models/episode.py. Every semantic write requires source + confidence — no exceptions.
```

### RAG-AGENT
```
READ: agents/rag-agent.md, skills/RAG.md
BUILD: rag/chunking.py, rag/ingest.py, rag/retrieve.py, and rag/kg.py if KG_REQUIRED.
Use the chunking strategy specified per source in the PRP — do not apply one default
to every source type.
```

### CONTEXT-AGENT
```
READ: agents/context-agent.md, skills/CONTEXT.md
BUILD: context/budget.py, context/assembler.py, context/compaction.py.
Priority order: system prompt > safety rules > current task > semantic memory >
RAG context > episodic memory > older conversation.
```

### TOOL-AGENT
```
READ: agents/tool-agent.md, skills/TOOLS.md
BUILD: tools/registry.py, tools/sandbox.py, and one file per tool from the PRP's
TOOLS table, correctly flagging requires_approval per the PRP.
```

### LLMOPS-AGENT
```
READ: agents/llmops-agent.md, skills/LLMOPS.md
BUILD: llmops/model_router.py (with the routing table from the PRP's tech stack),
llmops/tracer.py, llmops/cost_tracker.py, llmops/prompt_registry.py.
```

### After dispatching: block on TaskOutput for all 6, then run Validation Gate 1:
```bash
pytest backend/tests/test_harness.py backend/tests/test_memory.py backend/tests/test_rag.py -v
pytest backend/tests/test_context.py backend/tests/test_llmops.py -v
python -m app.tools.registry --validate-all-schemas
```
If the gate fails: retry the failing agent once with the error context. If it fails again, stop — do not proceed.

---

## STEP 3: PHASE 2 — Runtime agents + safety wiring (dispatch ALL in ONE response)

For EACH runtime agent defined in the PRP, dispatch a build task using HARNESS/CONTEXT/TOOL/RAG/MEMORY as available building blocks. Dispatch SAFETY-AGENT alongside, in the same response.

### Per runtime agent (repeat for each one in the PRP)
```
BUILD: agents/[agent_name].py — its system prompt (registered via llmops/prompt_registry.py,
not hardcoded inline), which tools it's allowed to call, its escalation condition,
and its model tier per the PRP.
Wire it into harness/loop.py's run_agent() via its config.
```

### SAFETY-AGENT
```
READ: agents/safety-agent.md, skills/SAFETY.md
BUILD: safety/approval_gate.py, safety/guardrails.py (refusal topics from the PRP),
safety/cost_cap.py, safety/kill_switch.py, safety/audit.py.
Wire the approval gate into every tool the PRP marked requires_approval=true.
```

### After dispatching: block on TaskOutput for all, then run Validation Gate 2:
```bash
pytest backend/tests/test_tools.py backend/tests/test_safety.py -v
python -m backend.app.safety.audit    # must report zero ungated risky tools
grep -rn "openai.ChatCompletion\|anthropic.Anthropic(" backend/app/ | grep -v "llmops/"  # should be empty
```
Any ungated `requires_approval` tool found here is a hard stop — escalate to the user immediately, do not proceed to Phase 3.

---

## STEP 4: PHASE 3 — Quality (dispatch 2 agents in ONE response)

### EVAL-AGENT
```
READ: agents/eval-agent.md, skills/EVALS.md
BUILD: evals/golden_set.jsonl (from the PRP's eval plan source), evals/metrics.py,
evals/llm_judge.py, evals/run_evals.py. Establish the baseline scores.
```

### LEARNING-AGENT
```
READ: agents/learning-agent.md, skills/LEARNING.md
BUILD: models/feedback.py, learning/feedback.py, learning/reflection_job.py,
learning/proposal_review.py, and finetune_export.py if in scope.
Enforce: system_prompt and safety_threshold changes ALWAYS route to human review.
```

### Final Validation
```bash
pytest backend/tests -v --cov=backend/app --cov-fail-under=80
pytest backend/evals -v --tb=short
python -m backend.app.safety.audit
python -m backend.evals.run_evals --compare-to-baseline
```

---

## STATUS DISPLAY
Show a status block after each phase (agent, ✅/🔄/⏳, time, key output count — e.g. tools registered, eval score).

## FINAL REPORT
Use the format in `agents/ORCHESTRATOR.md`, including "Manual steps still required" (API keys, prompt sign-off, golden set review with domain expert).

---

## ERROR HANDLING
1. Log the error with full context
2. Continue other parallel agents in the same phase where the failure is unrelated
3. Retry the failed agent once with the error appended
4. If still failing: mark as partial, do not proceed to the next phase
5. **Always escalate immediately, never retry-and-hope, for:** ungated `requires_approval` tools, missing/disabled refusal-topic guardrails, cost caps implemented as warnings instead of hard stops, and any eval regression beyond the PRP's threshold
6. User can resume: `/execute-prp [PRP] --resume`
