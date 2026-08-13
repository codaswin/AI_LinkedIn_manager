# PRP: AI LinkedIn Manager

## METADATA
| Field | Value |
|-------|-------|
| System | AI LinkedIn Manager |
| Type | Hybrid (multi-agent crew + deterministic automation) |
| Complexity | Medium-High — 5 runtime agents, 13 tools (6 requiring approval) routed through Composio, 5 RAG sources, no KG layer |

## SYSTEM OVERVIEW

Manages a professional's LinkedIn presence end-to-end: drafts on-brand posts, engages with the feed, drafts replies to comments/DMs, tracks connection requests, deletes stale/risky posts, produces a weekly performance digest, and researches AI/Agentic AI/Hermes/tooling developments on X (Twitter) to keep post topics current. All actual LinkedIn read/write operations, and all X research reads, go through **Composio's** managed integrations rather than hand-rolled API clients. Every action that publishes, deletes, or contacts a real third party is human-approval-gated — the system never posts, deletes, replies, messages, or connects autonomously, and it never posts or engages on X at all (read-only research only).

**MVP scope:**
- [ ] Content Strategist + Writer agents produce brand-voice-grounded post drafts
- [ ] Engagement Agent drafts comment/DM replies and connection-request suggestions for approval
- [ ] Research Agent surfaces AI/Agentic AI/Hermes/tooling updates from X, feeding the Strategist Agent's topic selection, polling daily by default
- [ ] Research Agent's X poll interval is a stored, API-backed setting (default: daily) — never a hardcoded constant, so it can be exposed in a UI later without a code change
- [ ] Human-approval flow blocks all `requires_approval` tools with no bypass
- [ ] Escalation path to a human when confidence < 0.75 or a refusal topic is detected

**Post-MVP:** dashboard UI control for the Research Agent's poll interval (and other agent-cadence settings), self-learning loop, connection-relationship knowledge graph, analytics-driven auto-scheduling.

## TECH STACK

| Layer | Technology | Skill reference |
|-------|-----------|------------------|
| Orchestration | CrewAI (role-based crew: Strategist, Writer, Engagement, Analytics, Research) | `skills/HARNESS.md` |
| Inference (primary) | Hosted API — Anthropic Claude | `skills/LLMOPS.md` |
| Inference (worker) | Self-hosted Hermes via vLLM (notification triage, priority scoring, X post triage/summarization) | `skills/LLMOPS.md` |
| RAG | FAISS, no KG layer for MVP | `skills/RAG.md` |
| Serving | FastAPI | `skills/HARNESS.md` |
| Memory | Postgres (episodic) + Redis (working) + FAISS (semantic) | `skills/MEMORY.md` |
| Context | Token-budgeted assembly, deliberate compaction | `skills/CONTEXT.md` |
| LinkedIn integration | Composio (managed LinkedIn actions: post, delete, comment, message, connect; owns OAuth + low-level rate limiting) | `skills/TOOLS.md` |
| X (Twitter) integration | Composio (read-only X search/timeline actions for research; separate connected-app scope from LinkedIn; no posting/replying/DM on X). Poll cadence defaults to daily, stored as a DB-backed setting rather than a hardcoded constant, so it can be surfaced in the observability dashboard UI later without a redeploy. | `skills/TOOLS.md` |
| Automation glue | n8n — polls Composio for notifications, triggers approved publish/delete jobs | `skills/HARNESS.md` |

## RUNTIME AGENTS

### 1. Content Strategist Agent
- **Goal:** Decide what to post about from the content calendar, RAG-retrieved trending topics, and gaps in recent posting history.
- **Inputs:** Content calendar entries, trending-topics RAG results, last 30 days of published posts (episodic memory)
- **Outputs:** Structured post brief (topic, angle, format, target date) → handed to Writer Agent
- **Model tier:** Hosted API, small/cheap tier (planning task)
- **Tools:** `search_knowledge_base`
- **Escalation:** None — output is an internal brief, not user-facing

### 2. Content Writer Agent
- **Goal:** Write full post copy in the user's brand voice, grounded in the Strategist's brief and the style guide.
- **Tools it can call:** `search_knowledge_base`, `draft_post`
- **RAG sources it queries:** Brand voice/style guide, user's past posts, industry news feed
- **Model tier:** Hosted API, primary tier (generation quality matters)
- **Escalation condition:** Brand-voice fidelity confidence < 0.75 → flag draft "needs human rewrite" instead of "ready to approve"

### 3. Engagement Agent
- **Goal:** Monitor notifications (comments, DMs, connection requests), draft replies/actions, scan feed for posts worth liking/commenting on.
- **Tools it can call:** `get_linkedin_notifications`, `search_knowledge_base`, `like_post`, `reply_to_comment`, `reply_to_dm`, `send_connection_request`
- **RAG sources it queries:** Past comment/DM threads, brand voice/style guide, connection relationship notes (semantic memory)
- **Model tier:** Hosted API primary tier for drafting; Hermes worker tier for notification triage/priority scoring
- **Escalation condition:** Sensitive-topic match (see refusal topics) or reply confidence < 0.75 → escalate to human, do not draft

### 4. Analytics & Reporting Agent
- **Goal:** Weekly digest of post performance (impressions, engagement rate, follower delta); flags underperforming or reputationally risky posts and suggests deletion.
- **Tools it can call:** `generate_analytics_report`, `search_knowledge_base`, `delete_post`
- **RAG sources it queries:** Past posts + engagement stats (episodic memory)
- **Model tier:** Hosted API, small/cheap tier (summarization)
- **Escalation condition:** Any `delete_post` suggestion always routes to the approval queue with full post content + reason attached, regardless of confidence — never auto-executed

### 5. Research Agent
- **Goal:** Track X (Twitter) for developments in AI, Agentic AI, Hermes, and AI tooling; produce research notes that feed the Content Strategist's trending-topics input.
- **Tools it can call:** `search_x_posts`, `save_research_note`, `search_knowledge_base`
- **RAG sources it queries/writes:** Writes to the X research feed; reads the industry news feed to avoid duplicate coverage
- **Model tier:** Hermes worker tier for high-volume triage/summarization of X posts; hosted API primary tier only for the final digest write-up
- **Poll interval:** Default **daily**, not hourly. Stored as a DB-backed, API-editable setting rather than a hardcoded constant, so a future dashboard UI can change the cadence without a redeploy.
- **Escalation condition:** None — read-only research, informational output only. No tool in this agent's registry can post, reply, like, or DM on X.

## TOOLS

> `get_linkedin_notifications`, `like_post`, `publish_post`, `schedule_post`, `delete_post`, `reply_to_comment`, `reply_to_dm`, and `send_connection_request` are all thin wrappers around **Composio** LinkedIn actions. `search_x_posts` is a Composio-backed X (Twitter) action, scoped read-only — no tool in this system posts, replies, or DMs on X. Composio handles OAuth/token refresh and its own rate limiting; our `requires_approval` gate and rate caps are enforced on top, not as a substitute.

| Tool | Purpose | Requires approval |
|------|---------|---------------------|
| `search_knowledge_base` | Query RAG index (brand voice, posts, news, threads, X research) | No |
| `get_linkedin_notifications` | Poll Composio for comments/DMs/connection requests (read-only) | No |
| `draft_post` | Create a queued draft post (not published) | No |
| `generate_analytics_report` | Summarize performance from stored engagement data | No |
| `search_x_posts` | Search X via Composio for AI/Agentic AI/Hermes/tooling updates (read-only) | No |
| `save_research_note` | Persist a research finding to semantic memory / RAG index | No (internal write only) |
| `like_post` | Like a post as the user, rate-capped, via Composio | No |
| `publish_post` | Publish a post immediately, via Composio | **Yes** |
| `schedule_post` | Queue a post to auto-publish at a future time, via Composio + n8n | **Yes** |
| `delete_post` | Delete a previously published post, via Composio | **Yes** — irreversible, requires explicit confirmation of the exact post |
| `reply_to_comment` | Post a public reply to a comment, via Composio | **Yes** |
| `reply_to_dm` | Send a private message to a third party, via Composio | **Yes** |
| `send_connection_request` | Send a connection request, via Composio | **Yes** |

## MEMORY ARCHITECTURE

- **Working (Redis):** current draft in progress, current notification thread being triaged, current approval-queue session state.
- **Episodic (Postgres):** last 12 months of published posts + engagement stats; last 90 days of comment/DM threads and their resolution (auto-approved / human-edited / escalated).
- **Semantic (FAISS + Postgres metadata):** brand voice/tone profile, topics the user cares about, per-connection relationship context, standing research interests (AI, Agentic AI, Hermes, tooling) that steer the Research Agent's X search terms.
- **Retention:** episodic post/engagement data → 12 months then archived; DM/comment thread *content* purged after 90 days unless flagged important. Every memory write carries `source` and `confidence` per CLAUDE.md non-negotiable #5 (storing memory without these fields is forbidden).

## RAG PIPELINE

| Source | Type | Update frequency | Chunking |
|--------|------|-------------------|----------|
| User's past LinkedIn posts | Structured | Continuous (on publish) | 1 chunk/post |
| Brand voice / style guide | Document | Static / on-upload | 500 tokens, semantic split |
| Industry news / trending topics | Document (RSS/API) | Daily | 1 chunk/article |
| Past comment/DM threads | Structured (Q&A) | Continuous | 1 chunk/thread |
| X (Twitter) AI/Agentic AI/Hermes/tooling research notes | Structured (via Composio) | Daily by default — poll interval is a user-editable setting, changeable from the UI without redeploying | 1 chunk/research note (deduped/summarized from source posts) |

**KG layer:** No for MVP. Post-MVP candidate: connection relationship graph (Person → Company → Relationship type) for engagement targeting.

## SAFETY REQUIREMENTS

- **Approval-gated tools:** `publish_post`, `schedule_post`, `delete_post`, `reply_to_comment`, `reply_to_dm`, `send_connection_request` — no bypass, no "just this once" (CLAUDE.md non-negotiable #3).
- **Confidence threshold:** 0.75 — below this, escalate rather than present as ready-to-approve. `delete_post` always escalates regardless of confidence, since deletion is irreversible.
- **`delete_post` approval prompt requirement:** must render the full post content, publish date, and engagement stats — never a bare post ID — and never supports batch/bulk delete.
- **Refusal topics:** political endorsements, health/financial/legal advice, disparagement of a named individual or competitor, engagement-bait/misinformation, authorship-misrepresenting impersonation.
- **Cost/rate caps:** $10/day LLM spend; ≤5 connection requests/day; ≤20 comment/DM replies/day; ≤3 posts/day; ≤3 delete actions/day (LinkedIn ToS + Composio rate limits).
- **Sole integration path:** all LinkedIn access goes through Composio — no direct/unofficial API calls or scraping, so auth, consent, and rate limits stay centrally enforced.
- **X (Twitter) is read-only:** the Research Agent's X access is search/read only via Composio. No tool exists to post, reply, like, retweet, or DM on X — posting on X is out of scope for this system entirely.

## EVALUATION PLAN

- **Golden set:** 50 curated (topic → ideal post) pairs + 30 comment/DM scenarios with ideal replies, curated by the user from real history.
- **Metrics:** brand-voice fidelity (LLM-as-judge vs. style guide), groundedness (cited stats/news traceable to a retrieved source), escalation precision (escalates when it should, doesn't over-escalate).
- **Regression policy:** no eval score may drop >5% between versions without explicit user sign-off.

## SELF-LEARNING SCOPE

- **Feedback signals:** approve/reject/edit actions on drafts; actual engagement metrics measured 7 days post-publish.
- **Auto-apply:** RAG retrieval ranking weights; few-shot examples drawn from top-performing past posts.
- **Human review required:** any system-prompt or brand-voice-profile change; any new tool; any change to approval-gating rules or confidence thresholds.

## PHASE EXECUTION PLAN

**Phase 1 (Parallel — Foundation):**
- `harness-agent` — CrewAI-based agent loop, state machine, stopping conditions for the 5 runtime agents; scheduler reads each agent's cadence (e.g. Research Agent's poll interval) from the settings store rather than a hardcoded constant
- `memory-agent` — Postgres/Redis/FAISS stores per Memory Architecture above, plus a small `agent_settings` table (key, value, updated_by, updated_at) seeded with `research_agent.poll_interval = daily`, exposed via a FastAPI endpoint for the future dashboard UI to read/update
- `rag-agent` — ingestion + retrieval for the 5 RAG sources, no KG layer
- `context-agent` — token budget assembly for Claude primary + Hermes worker tiers
- `tool-agent` — registry + sandboxed execution for the 13 tools, Pydantic schemas, `requires_approval` flags wired in; LinkedIn- and X-facing tools implemented as Composio action wrappers (Composio SDK client, per-app connected-account setup, X scoped read-only at the tool-schema level)
- `llmops-agent` — model router (Claude primary / Hermes worker split), tracer skeleton, cost tracking toward $10/day cap

**Phase 2 (Parallel per runtime agent + safety wiring):**
- Backend build: Content Strategist Agent
- Backend build: Content Writer Agent
- Backend build: Engagement Agent
- Backend build: Analytics & Reporting Agent (includes `delete_post` suggestion logic)
- Backend build: Research Agent (X search/triage via Hermes worker tier, research-note write-up via Claude primary tier; polls at the interval read from `agent_settings`, default daily)
- `safety-agent` — approval gates on all 6 `requires_approval` tools, confidence-threshold escalation (0.75), mandatory escalation for `delete_post` regardless of confidence, refusal-topic detection, rate/cost cap enforcement, and a registry-level check that no X tool carries a write/post capability

**Phase 3 (Parallel — Quality):**
- `eval-agent` — golden set (50 posts + 30 reply scenarios) + eval harness (brand-voice fidelity, groundedness, escalation precision), wired as a regression gate at 5% threshold
- `learning-agent` — feedback capture (approve/reject/edit + 7-day engagement lag metric), reflection job for retrieval weights / few-shot examples, human-review queue for prompt/tool/threshold changes

## VALIDATION GATES

| Gate | Commands |
|------|----------|
| 1 | `pytest backend/tests/test_harness.py backend/tests/test_memory.py backend/tests/test_rag.py -v`; `python -m backend.app.safety.audit` |
| 2 | `pytest backend/tests/test_tools.py backend/tests/test_safety.py -v`; `grep -rn "requires_approval" backend/app/tools | grep -v "gated"` (confirm no ungated approval tool) |
| 3 | `pytest backend/evals -v --tb=short`; compare-to-baseline (fail if any metric regresses >5%) |
| Final | `ruff check backend/ && mypy backend/app --ignore-missing-imports`; `pytest backend/tests --cov --cov-fail-under=80`; `python -m backend.app.safety.audit`; load test within $10/day cost cap and rate caps |

## ENVIRONMENT VARIABLES

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/db
REDIS_URL=redis://localhost:6379/0
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-ant-xxx
HERMES_ENDPOINT=http://localhost:8001/v1
VECTOR_DB_PATH=./data/faiss_index
KG_BACKEND=networkx
LLM_COST_BUDGET_DAILY_USD=10
TRACE_SINK=local

# Project-specific
COMPOSIO_API_KEY=xxx
COMPOSIO_LINKEDIN_CONNECTED_ACCOUNT_ID=xxx
COMPOSIO_X_CONNECTED_ACCOUNT_ID=xxx
LINKEDIN_API_RATE_LIMIT_CONNECTIONS_DAILY=5
LINKEDIN_API_RATE_LIMIT_REPLIES_DAILY=20
LINKEDIN_API_RATE_LIMIT_POSTS_DAILY=3
LINKEDIN_API_RATE_LIMIT_DELETES_DAILY=3
X_RESEARCH_POLL_INTERVAL_DEFAULT=daily   # seed value only — live value lives in the agent_settings DB table, editable via API/UI
N8N_WEBHOOK_URL=http://localhost:5678/webhook/linkedin-poll
```

## NEXT STEP

/execute-prp PRPs/ai-linkedin-manager-prp.md
