# INITIAL.md — Define Your Agent System

> Fill this out, then run `/generate-prp INITIAL.md`. This is the single source of truth for what the system does, who its runtime agents are, and what production guarantees it must meet.

---

## SYSTEM

**Name:** [Your agent system name]

**Purpose:** [What problem does it solve? What does "done" look like for a single user interaction?]

**Type:** Single-agent assistant / Multi-agent crew / Autonomous workflow / Hybrid (agents + deterministic automation)

**Example (for reference):** "CAT mentorship WhatsApp doubt-bot — resolves student doubts against the mentor's specific curriculum, escalates to a human mentor when confidence is low, and logs doubt analytics."

---

## TECH STACK

| Layer | Choice |
|-------|--------|
| Orchestration | Autogen / CrewAI |
| Inference (primary) | Hosted API (OpenAI/Anthropic) |
| Inference (worker, optional) | Self-hosted (Hermes via vLLM/Ollama) |
| RAG | FAISS + [KG layer: yes/no] |
| Serving | FastAPI |
| Memory store | Postgres + Redis + FAISS/Chroma |
| Automation glue | n8n (optional) |

---

## RUNTIME AGENTS

> The actual AI agents your system runs — not the build-time agents in `/agents`.

### Agent 1: [e.g. Router Agent]
**Goal:** [What decision does it make?]
**Inputs:** [What it receives]
**Outputs:** [What it produces / hands off]
**Model:** [e.g. small/cheap model — this is a routing task, not generation]

### Agent 2: [e.g. Doubt-Resolution Specialist]
**Goal:** [...]
**Tools it can call:** [list]
**RAG sources it queries:** [list]
**Escalation condition:** [e.g. confidence < 0.7 → hand off to human]

### Agent 3: [Add as needed]

---

## TOOLS

| Tool | Purpose | Requires human approval? |
|------|---------|---------------------------|
| `search_knowledge_base` | Query RAG index | No |
| `send_whatsapp_message` | Reply to user | No |
| `escalate_to_human` | Hand off to a real mentor | No (this IS the safe path) |
| `update_student_record` | Write to CRM | Yes — external system mutation |
| [Add project-specific tools] | | |

---

## MEMORY REQUIREMENTS

**Working memory:** [What must persist across the current session/task? e.g. current conversation, current sub-goal]

**Episodic memory:** [What past interactions matter? e.g. "student's last 5 doubts and whether they were resolved"]

**Semantic memory:** [What long-term facts should the system remember about a user/entity? e.g. "student's weak topics", "mentor's teaching preferences"]

**Retention:** [How long does each tier persist? Any deletion/privacy requirements?]

---

## RAG SOURCES

| Source | Type | Update frequency | Chunking strategy |
|--------|------|-------------------|--------------------|
| [e.g. Mentor's course transcripts] | Document | Static / on-upload | [e.g. 500 tokens, semantic split] |
| [e.g. Past resolved doubts] | Structured (Q&A pairs) | Continuous | [1 chunk per Q&A] |

**Knowledge Graph layer needed?** [Yes/No — if yes, what entities/relationships? e.g. Topic → Subtopic → Question]

---

## SAFETY & APPROVAL REQUIREMENTS

- [ ] Actions that mutate external systems (CRM, payments, messages to third parties) require approval: [list]
- [ ] Confidence threshold below which the system must escalate rather than answer: [value]
- [ ] Topics/requests the system must always refuse or redirect: [list]
- [ ] Rate/cost caps: [e.g. max $X/day in LLM spend, max N tool calls per task]

---

## EVALUATION CRITERIA

**Golden test set source:** [e.g. 50 real past doubts with correct resolutions, curated by the mentor]

**Metrics that matter:**
- [ ] Groundedness (answer supported by retrieved source, not hallucinated)
- [ ] Escalation precision (escalates when it should, doesn't over-escalate)
- [ ] [Domain-specific metric, e.g. "correctly cites the right chapter"]

**Regression policy:** [e.g. "no eval score may drop >5% between versions without explicit sign-off"]

---

## SELF-LEARNING SCOPE

**Feedback signals available:** [e.g. thumbs up/down, human mentor corrections, resolved-without-escalation rate]

**What improves automatically:** [e.g. retrieval ranking weights, few-shot examples in the prompt]

**What requires human review before deploying:** [e.g. any change to the system prompt, any new tool]

---

## MVP SCOPE

Must Have:
- [ ] [Core agent behavior 1]
- [ ] [Core agent behavior 2]
- [ ] Escalation path to a human

Post-MVP:
- [ ] Self-learning loop active
- [ ] Knowledge graph layer

---

## ACCEPTANCE CRITERIA

- [ ] Agent answers grounded questions correctly against the golden set (target: [X]%)
- [ ] Agent escalates instead of guessing when confidence is below threshold
- [ ] No `requires_approval` tool executes without explicit approval, verified by test
- [ ] Full trace (tokens, cost, latency) exists for every request
- [ ] System stays within daily cost budget under load test

---

## FORBIDDEN

- [Anything explicitly out of scope — e.g. "must never give students exam leak content", "must never message a student outside the mentor's approved hours"]

---

## RUN

```bash
/generate-prp INITIAL.md
/execute-prp PRPs/[name]-prp.md
```
