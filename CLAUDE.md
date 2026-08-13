# CLAUDE.md — Project Rules

> Rules Claude Code follows in every conversation on this project.

---

## Tech Stack (default — override in INITIAL.md if needed)

- **Orchestration:** Autogen (multi-agent) — CrewAI as an alternate backend where simpler role-based crews fit better
- **Inference:** Hosted API (OpenAI/Anthropic/Claude) as primary — self-hosted open model (e.g. Hermes via vLLM/Ollama) as a worker/margin-protection layer for high-volume, low-risk tasks
- **RAG:** FAISS for vector retrieval; optional Knowledge Graph layer (NetworkX for lightweight, Neo4j for production scale) for KG RAG
- **Serving:** FastAPI
- **Persistent memory:** Postgres (episodic + structured memory), Redis (working memory / session state), FAISS/Chroma (semantic long-term memory)
- **Tracing / LLMOps:** structlog + a lightweight custom tracer (pluggable to Langfuse/Phoenix if the project wants a hosted option)
- **Evals:** pytest-based harness + LLM-as-judge
- **Automation glue (optional):** n8n for external trigger workflows
- **Observability dashboard (optional):** React

---

## Project Structure

```
project/
├── backend/
│   └── app/
│       ├── main.py, config.py, database.py
│       ├── harness/          # the agent loop / runtime
│       ├── agents/           # runtime agent definitions (roles, prompts, goals)
│       ├── tools/             # tool schemas + implementations
│       ├── memory/           # working, episodic, semantic memory
│       ├── rag/               # ingestion + retrieval pipeline
│       ├── context/           # context assembly + budget management
│       ├── safety/            # guardrails, approval gates
│       ├── evals/             # eval harness, golden datasets
│       ├── llmops/            # model routing, tracing, cost tracking
│       ├── learning/          # feedback capture, reflection
│       └── tests/
├── frontend/                  # optional observability dashboard
├── skills/            # 9 skill files — full runnable code
├── agents/            # build-time agent definitions (Claude Code agents)
├── .claude/commands/  # /setup-project, /generate-prp, /execute-prp
└── PRPs/
```

**Important distinction:** `agents/` at the project root are the **build-time agents** (Claude Code sub-agents that write code). `backend/app/agents/` are the **runtime agents** — the actual AI agents your product runs in production. Don't conflate them.

---

## Non-Negotiable Rules

1. **Every LLM call goes through the harness's `run_step()` — never call the LLM API directly from a tool or router.** This is what makes tracing, cost tracking, and retries possible.
2. **Every tool call is logged with inputs, outputs, latency, and cost** before the loop continues.
3. **Any action tagged `requires_approval` in a tool's schema blocks until a human approves it** — no exceptions, no "just this once."
4. **Context assembly always respects the token budget** — silently exceeding it and truncating mid-response is a defect, not a fallback.
5. **No agent ships without at least one eval suite passing** — a new capability without a golden-set test is not done.

---

## Code Standards

### Python
```python
# Type hints required. Every tool function has a Pydantic schema.
def search_knowledge_base(query: str, top_k: int = 5) -> list[RetrievedChunk]:
    pass

# Async for anything calling an LLM or external API
async def run_step(state: AgentState) -> AgentState:
    pass
```

---

## Forbidden

- Direct LLM API calls outside the harness/llmops layer
- Tools without a Pydantic input schema
- Silent context truncation — must summarize/compact deliberately (see `skills/CONTEXT.md`)
- Autonomous execution of any `requires_approval` tool
- Storing memory without a `source` and `confidence` field (untraceable memory is a liability)
- Shipping a new agent capability without an eval added to `evals/`
- Hardcoded API keys/model names in code — env vars + `llmops/model_router.py`

---

## Workflow

```
1. Edit INITIAL.md (define the agent system, its agents, tools, memory, RAG sources)
2. /generate-prp INITIAL.md
3. /execute-prp PRPs/[name]-prp.md
```

---

## Skills

| Domain | Skill |
|--------|-------|
| Harness & loop engineering | `skills/HARNESS.md` |
| RAG engineering | `skills/RAG.md` |
| Persistent memory | `skills/MEMORY.md` |
| Context engineering | `skills/CONTEXT.md` |
| Tool integration | `skills/TOOLS.md` |
| Safety & guardrails | `skills/SAFETY.md` |
| Evals | `skills/EVALS.md` |
| LLM ops | `skills/LLMOPS.md` |
| Self-learning | `skills/LEARNING.md` |

---

## Agents (build-time)

| Agent | Role |
|-------|------|
| HARNESS-AGENT | Builds the agent loop, state machine, stopping conditions |
| RAG-AGENT | Ingestion + retrieval pipeline, KG RAG layer |
| MEMORY-AGENT | Working/episodic/semantic memory stores + read-write policy |
| CONTEXT-AGENT | Context assembly, token budgeting, compaction |
| TOOL-AGENT | Tool schemas, execution, sandboxing |
| SAFETY-AGENT | Guardrails, approval gates, kill switch |
| EVAL-AGENT | Golden datasets, eval harness, LLM-as-judge |
| LLMOPS-AGENT | Model routing, tracing, cost/latency tracking, deployment |
| LEARNING-AGENT | Feedback capture, reflection loop, self-improvement |

---

## Validation

```bash
ruff check backend/ && mypy backend/app --ignore-missing-imports
pytest backend/tests --cov --cov-fail-under=80
pytest backend/evals -v --tb=short          # eval suite — must pass before merge
python -m backend.app.safety.audit          # confirms no ungated risky tools
```

---

## Environment Variables

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/db
REDIS_URL=redis://localhost:6379/0
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-ant-xxx
HERMES_ENDPOINT=http://localhost:8001/v1        # self-hosted worker model, if used
VECTOR_DB_PATH=./data/faiss_index
KG_BACKEND=networkx                              # or neo4j://...
LLM_COST_BUDGET_DAILY_USD=50
TRACE_SINK=local                                 # or langfuse/phoenix endpoint
```
