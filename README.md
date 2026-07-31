# Agentic AI System Template

> Clone. Define. Build. Production-grade agent systems — not a LangChain demo notebook.

---

## Quick Start

```bash
git clone <this-template-url> my-agent-system
cd my-agent-system

/setup-project
/generate-prp INITIAL.md
/execute-prp PRPs/[name]-prp.md
```

---

## What You Get

- **Harness engineering:** a real agent loop (perceive → plan → act → observe) with explicit stopping conditions, retries, and backoff — not an unbounded `while True`
- **RAG engineering:** ingestion + retrieval pipeline over FAISS, with an optional Knowledge Graph layer for KG RAG
- **Persistent memory:** working memory (Redis), episodic memory (Postgres), semantic long-term memory (vector store) — each with a defined read/write policy
- **Context engineering:** deliberate context assembly with a token budget and compaction strategy, not silent truncation
- **Tool integration:** schema-validated tools, sandboxed execution, human-approval gates on risky actions
- **Safety:** guardrails, kill switch, cost/rate caps
- **Evals:** a golden-dataset harness with LLM-as-judge, run as a regression gate
- **LLM ops:** model routing (cheap model for routing, strong model for generation), full tracing of every call, cost tracking, prompt versioning
- **Self-learning:** structured feedback capture and a reflection loop that improves retrieval/prompts without silently changing behavior unreviewed

---

## How It Works

```
INITIAL.md → /generate-prp → PRP blueprint → /execute-prp → Full System

Phase 1 (Parallel — Foundation):
├─ HARNESS-AGENT   → Agent loop, state machine
├─ MEMORY-AGENT    → Working/episodic/semantic stores
├─ RAG-AGENT       → Ingestion + retrieval pipeline
├─ CONTEXT-AGENT   → Context assembly + budget
├─ TOOL-AGENT      → Tool registry + execution
└─ LLMOPS-AGENT    → Model router, tracer skeleton

Phase 2 (Parallel per runtime agent, from INITIAL.md's RUNTIME AGENTS):
├─ Each runtime agent's prompt, tools, escalation logic
└─ SAFETY-AGENT    → Guardrails + approval gates wired into every risky tool

Phase 3 (Parallel — Quality):
├─ EVAL-AGENT      → Golden set + eval harness
├─ LEARNING-AGENT  → Feedback capture + reflection job
└─ REVIEW-AGENT... (see review checklist in eval-agent.md)
```

---

## Files

| File | Purpose |
|------|---------|
| `INITIAL.md` | System purpose, runtime agents, tools, memory, RAG sources, safety rules |
| `CLAUDE.md` | Non-negotiable rules (every LLM call traced, every risky tool gated) |
| `skills/*.md` | 9 files — real, runnable code for each engineering domain |
| `agents/*.md` | 10 build-time agent definitions |
| `.claude/commands/` | `/setup-project`, `/generate-prp`, `/execute-prp` |

---

## Skills (9 files)

| Skill | Contains |
|-------|----------|
| `HARNESS.md` | The agent loop, stopping conditions, retry/backoff |
| `RAG.md` | Chunking, embedding, FAISS index, retrieval, KG RAG |
| `MEMORY.md` | Working/episodic/semantic memory read-write code |
| `CONTEXT.md` | Token budgeting, prompt assembly, compaction |
| `TOOLS.md` | Tool schema pattern, execution, sandboxing |
| `SAFETY.md` | Guardrails, approval-gate middleware, kill switch |
| `EVALS.md` | Eval harness, golden dataset format, LLM-as-judge |
| `LLMOPS.md` | Model router, tracer, cost tracking, prompt versioning |
| `LEARNING.md` | Feedback capture, reflection job, safe self-improvement |

---

## Run Locally

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# Ingest RAG sources
python -m app.rag.ingest --source ./data/docs

# Run the eval suite
pytest backend/evals -v

docker-compose up -d   # api, worker, redis, postgres, vector store
```
