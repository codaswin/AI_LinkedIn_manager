# Setup Project Command

You are a **Project Setup Wizard**. Interactively collect information about the user's agent system and generate `INITIAL.md` and `CLAUDE.md`.

## Process

### STEP 1: Ask in grouped sets

**Group 1 — System purpose**
1. System name?
2. One-sentence purpose — what does it do, end to end, for one user interaction?
3. Single agent, multi-agent crew, or autonomous workflow?

**Group 2 — Tech stack** (AskUserQuestion, each with a recommended default)
```yaml
questions:
  - question: "Orchestration framework?"
    options:
      - label: "Autogen (Recommended for multi-agent)"
      - label: "CrewAI (Recommended for role-based crews)"
      - label: "Custom harness only, no framework"
  - question: "Primary inference?"
    options:
      - label: "Hosted API — OpenAI/Anthropic (Recommended)"
      - label: "Self-hosted open model (e.g. Hermes via vLLM)"
      - label: "Hybrid — hosted for reasoning, self-hosted for high-volume tasks"
  - question: "Knowledge Graph RAG needed?"
    options:
      - label: "No — vector RAG is sufficient"
      - label: "Yes — relationships between entities matter (e.g. topic hierarchies)"
```

**Group 3 — Runtime agents**
For each runtime agent the system needs: its goal, what it can decide, what it hands off, and roughly which model tier fits (cheap/routing vs. strong/reasoning).

**Group 4 — Tools**
List each tool the system needs, and mark which ones mutate external systems (these will require human approval by default).

**Group 5 — Memory**
What must the system remember: within a session (working), across past interactions (episodic), and long-term about an entity (semantic)? Any retention/privacy limits?

**Group 6 — RAG sources**
What documents/data will be retrieved from? For each: type, how often it updates, and roughly how it should be chunked.

**Group 7 — Safety**
```yaml
questions:
  - question: "What's the confidence threshold below which the system should escalate to a human instead of answering?"
    options:
      - label: "High bar — escalate often (safer, more human load)"
      - label: "Moderate — escalate only on clear uncertainty"
      - label: "Low bar — answer confidently, escalate rarely"
```
Also ask: any topics it must always refuse, any daily cost cap.

**Group 8 — Evaluation & self-learning**
What's the golden test set source? What metrics matter? What feedback signals exist, and what's allowed to auto-improve vs. always needing human review?

---

### STEP 2: Generate INITIAL.md

Fill the template exactly as defined in this repo's `INITIAL.md` — every section populated, no bracketed placeholders left in the output.

### STEP 3: Generate CLAUDE.md

Use the base `CLAUDE.md` as the starting point; adjust the Tech Stack table only if the user chose non-default options. Keep the 5 Non-Negotiable Rules intact regardless of stack choice — they apply to any agent system.

### STEP 4: Confirm and summarize

```
═══════════════════════════════════════════════════════════
              AGENT SYSTEM SETUP COMPLETE
═══════════════════════════════════════════════════════════
System: [Name]
Runtime agents: [list]
Tools: [count] ([count] requiring approval)
RAG sources: [count]
KG RAG: [yes/no]

FILES GENERATED:
├─ INITIAL.md
└─ CLAUDE.md

NEXT: /generate-prp INITIAL.md
═══════════════════════════════════════════════════════════
```

## Important Notes
- Be conversational, group questions, don't fire everything at once
- Always pre-select the recommended default
- If the user is unsure about memory/RAG/safety details, offer the CAT-mentorship example in INITIAL.md as a concrete reference point
