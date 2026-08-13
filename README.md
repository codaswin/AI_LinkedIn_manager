# 🤖 AI LinkedIn Manager

> A multi-agent system that manages a professional's LinkedIn presence end-to-end — drafting on-brand posts, engaging with the feed, replying to comments and DMs, tracking connections, reporting on performance, and researching AI/agentic-AI developments across six independent sources — with every public, irreversible, or third-party-contacting action gated behind explicit human approval. No exceptions, verified by test.

<p align="center">
  <em>5 runtime agents · 19 tools (6 require human approval) · 6 research sources · 497 tests · 88.33% coverage</em>
</p>

---

## Table of Contents

1. [What This Actually Is](#what-this-actually-is)
2. [Design Philosophy](#design-philosophy)
3. [System Architecture](#system-architecture)
4. [The 5 Runtime Agents](#the-5-runtime-agents)
5. [The Multi-Source Research System](#the-multi-source-research-system)
6. [Safety & the Human-Approval Gate](#safety--the-human-approval-gate)
7. [Tool Registry](#tool-registry)
8. [Memory Architecture](#memory-architecture)
9. [RAG Pipeline](#rag-pipeline)
10. [Eval Harness](#eval-harness)
11. [Self-Learning Loop](#self-learning-loop)
12. [End-to-End Workflows](#end-to-end-workflows)
13. [A Note on "Animations"](#a-note-on-animations)
14. [Tech Stack](#tech-stack)
15. [Project Structure](#project-structure)
16. [Model Routing & Cost Controls](#model-routing--cost-controls)
17. [Environment Variables](#environment-variables)
18. [Getting Started](#getting-started)
19. [Dashboard (Frontend)](#dashboard-frontend)
20. [Testing & Validation Gates](#testing--validation-gates)
21. [Roadmap](#roadmap)
22. [How This Was Built](#how-this-was-built)

---

## What This Actually Is

This is **not** a chatbot wrapper. It's a small society of narrowly-scoped agents, each with one job, a fixed toolset, and a hard-coded escalation rule, coordinated through a single choke-point agent loop. Nothing in this system can post, delete, message, or connect on a real person's behalf without a human explicitly clicking "approve" on the exact content that would go out.

Concretely, it:

- **Decides what to post about** by grounding topic selection in retrieved research, brand voice, and the last 30 days of published posts (Content Strategist)
- **Writes full post drafts** in the user's brand voice, self-scores its own confidence, and either queues the draft for approval or flags it "needs human rewrite" (Content Writer)
- **Monitors comments, DMs, and connection requests**, drafts replies, and screens every one for five categories of sensitive topics before it ever reaches a draft (Engagement)
- **Produces a weekly performance digest** and flags stale/underperforming/risky posts for possible deletion — a suggestion that *always* routes to a human, regardless of how confident the system is (Analytics & Reporting)
- **Tracks what's happening in AI** across Hacker News, Reddit, GitHub, Product Hunt, RSS feeds, and the general web (X/Twitter optional, off by default) to keep the Content Strategist's topic choices current (Research)

Every one of those five agents shares one rule without exception: **no agent holds a reference to an LLM client and calls it directly.** Every single model call in this entire codebase goes through one function — `harness.loop.run_step()` — which is what makes tracing, cost tracking, retries, and tool-call logging actually enforceable rather than aspirational.

---

## Design Philosophy

Four decisions shape everything else in this repo:

**1. Approval is structural, not a suggestion.** `requires_approval=True` on a tool isn't a flag some code path checks when convenient — `tools/registry.execute_tool()` refuses to run a gated tool without an explicit `approved=True`, and there is exactly **one function in the entire codebase** permitted to flip that flag: `safety.approval_gate.approve()`. A static audit (`python -m app.safety.audit`) fails the build if that ever stops being true.

**2. Cost-consciousness is a first-class design constraint, not an afterthought.** The Research Agent was originally X (Twitter)-only. X's API got expensive, so it was refactored into six sources — five of which need no paid API key at all (Hacker News, RSS, and DuckDuckGo web search need *nothing*; GitHub and Product Hunt work at generous free tiers). X is still available, just opt-in.

**3. Self-improvement is reviewed, never silent.** The learning loop can auto-apply a retrieval-weight tweak or an additive few-shot example on its own. It can **never** auto-apply a change to a system prompt, the brand-voice profile, a new tool, an approval-gating rule, or a confidence threshold — regardless of how confident the reflection job's own analysis is. That's enforced in code (`proposal_review.submit_proposal()`), not just policy.

**4. Every agent is testable without a live model — even now that a live one exists.** Every agent function takes an injectable `llm_client`, and every one of this repo's tests still runs against a fake; the one real implementation (`model_router.route_and_call`, wired to Anthropic for primary/cheap and Hermes/vLLM for the worker tier) is itself just another value that fits the same `llm_client` slot. That was a deliberate sequencing choice: build and prove the harness, the safety gates, and the eval/learning infrastructure first — against fakes — and only then wire in a real model, so "real" never means "suddenly untestable."

---

## System Architecture

```mermaid
flowchart TB
    subgraph Runtime["🎯 5 Runtime Agents"]
        direction LR
        CS["Content<br/>Strategist"]
        CW["Content<br/>Writer"]
        EN["Engagement"]
        AN["Analytics &<br/>Reporting"]
        RE["Research"]
    end

    subgraph Harness["⚙️ Harness — the single choke point"]
        RunStep["run_step()<br/><i>the ONLY function allowed to call an LLM</i>"]
        Stop["stopping_conditions.py<br/>budget / iteration / escalation"]
    end

    subgraph Safety["🛡️ Safety Layer"]
        Guard["guardrails.py<br/>5 refusal-topic detectors"]
        Gate["approval_gate.py<br/>the ONLY path to execute a gated tool"]
        Kill["kill_switch.py"]
        Cap["cost_cap.py<br/>$/day + per-action rate caps"]
    end

    subgraph Tools["🔧 19 Tools (registry.py)"]
        direction LR
        ReadTools["13 read/internal-write<br/>no approval needed"]
        WriteTools["6 write tools<br/>ALWAYS require approval"]
    end

    subgraph Memory["🧠 Memory"]
        Working["Working (Redis)"]
        Episodic["Episodic (Postgres)"]
        Semantic["Semantic (FAISS)"]
    end

    subgraph Research6["🔍 6 Research Sources"]
        direction LR
        HN[Hacker News]
        RD[Reddit]
        GH[GitHub]
        PH[Product Hunt]
        RSS[RSS Feeds]
        WEB[Web / DuckDuckGo]
        X["X — optional"]
    end

    subgraph Quality["📊 Phase 3 — Quality"]
        Eval["Eval Harness<br/>golden sets + LLM judge<br/>+ 5% regression gate"]
        Learn["Self-Learning Loop<br/>feedback → reflection → review queue"]
    end

    LinkedIn(("LinkedIn<br/>via Composio"))
    Human{{"👤 Human"}}

    Runtime --> RunStep
    RunStep --> Stop
    Runtime --> Tools
    RE --> Research6
    WriteTools -.->|blocked until| Gate
    Gate -->|approve/reject| Human
    Gate -->|approved only| LinkedIn
    ReadTools --> LinkedIn
    Runtime --> Guard
    Guard -->|refusal topic| Human
    Runtime --> Memory
    Runtime --> Eval
    Eval --> Learn
    Learn -.->|human review| Human

    style Gate fill:#7c3aed,color:#fff
    style Guard fill:#dc2626,color:#fff
    style RunStep fill:#0891b2,color:#fff
    style Human fill:#16a34a,color:#fff
```

The shape to internalize: **every arrow that touches LinkedIn or a real third party passes through the purple approval gate first.** There is no other path.

---

## The 5 Runtime Agents

| # | Agent | Model Tier | Tools It Can Call | Escalation Condition |
|---|-------|-----------|--------------------|-----------------------|
| 1 | **Content Strategist** | Cheap (planning task) | `search_knowledge_base` | None — output is an internal brief, never user-facing |
| 2 | **Content Writer** | Primary | `search_knowledge_base`, `draft_post` | Brand-voice confidence < **0.75** → `needs_human_rewrite`, never queued |
| 3 | **Engagement** | Worker (triage) / Primary (draft) | `get_linkedin_notifications`, `search_knowledge_base`, `like_post`, `reply_to_comment`, `reply_to_dm`, `send_connection_request` | Sensitive-topic match **or** confidence < 0.75 → escalate, no draft produced |
| 4 | **Analytics & Reporting** | Cheap (digest) | `generate_analytics_report`, `search_knowledge_base`, `delete_post` | `delete_post` suggestions **always** escalate, unconditionally |
| 5 | **Research** | Worker (triage) / Primary (digest, synthesis) | 6 source tools + `save_research_note`, `search_knowledge_base` | None — read-only, informational only |

### 1. Content Strategist Agent

Decides **what** to post about, never writes a word of copy. Pulls the content calendar, RAG-retrieved trending topics (including the Research Agent's findings), and the last 30 days of published posts; de-duplicates against recently-covered topics using a keyword-overlap heuristic (falls back to the full retrieved set if *everything* overlaps, rather than handing the next agent nothing). Output is one structured `PostBrief`: `{topic, angle, format, target_publish_date}`.

### 2. Content Writer Agent

Turns a `PostBrief` into full post copy, grounded in the brand-voice/style-guide RAG source, past posts, and industry news — then **self-scores its own confidence** that the draft matches brand voice. There is no partial credit: `confidence >= 0.75` → submitted to the human approval queue with either `publish_post` or `schedule_post` (chosen automatically based on whether the brief carries a `target_publish_date`); below that → returned as `needs_human_rewrite`, never touching the approval queue at all.

### 3. Engagement Agent

Watches comments, DMs, and connection requests. For every notification, the **first** check — before any drafting happens — is a regex-based refusal-topic scan (`guardrails.matches_refusal_topic`) across five categories: political endorsements, health/financial/legal advice, disparagement of a named individual or competitor, engagement-bait/misinformation, and impersonation requests. A match means immediate escalation with zero draft generated. If clean, it drafts a reply grounded in past comment/DM threads and brand voice, scores confidence, and either escalates (< 0.75) or queues for approval. `like_post` is the one action in this agent's toolkit that executes directly — it's rate-capped and reversible, not identity-risking.

### 4. Analytics & Reporting Agent

Produces a weekly digest (impressions, engagement rate, follower delta — all deterministic, pulled straight from stored data, never re-derived by the LLM) and uses the model only for the judgment call: which posts look underperforming, stale, or reputationally risky enough to flag. `suggest_deletion()` has **no confidence parameter at all** — there's no code path by which a "the model is very sure" argument could ever skip the approval queue for a delete action, and the approval prompt is required to carry the full post content, publish date, and engagement stats (never a bare post ID).

### 5. Research Agent

The most heavily reworked piece of this system — see the next section.

---

## The Multi-Source Research System

**Origin story:** this agent started as an X (Twitter)-only research feed. X's API is expensive at any real volume, so it was refactored into a modular, six-source system where X is available but never the default.

```mermaid
flowchart LR
    Query(["Research query,<br/>e.g. 'AI agents'"])

    subgraph Selection["1️⃣ Source Selection — cheap keyword heuristic, NOT an LLM call"]
        Rule1["open-source/framework/tool<br/>→ GitHub, HN, Reddit, Web"]
        Rule2["product/SaaS/launch<br/>→ Product Hunt, HN, Web, Reddit"]
        Rule3["reaction/opinion/discussion<br/>→ Reddit, HN, GitHub, Web"]
        Rule4["announcement/coverage/news<br/>→ RSS, Web, HN"]
        Default["no match →<br/>HN, Web, GitHub, Reddit"]
    end

    subgraph Fetch["2️⃣ Concurrent Fetch — asyncio.gather, one failure never sinks the run"]
        direction TB
        HN2["🟠 Hacker News<br/>Firebase API, no key"]
        RD2["🔴 Reddit<br/>OAuth2 client-credentials"]
        GH2["⚫ GitHub<br/>REST search, optional token"]
        PH2["🟣 Product Hunt<br/>GraphQL v2"]
        RSS2["🟢 RSS/Atom<br/>feedparser, 8 default feeds"]
        WEB2["🔵 DuckDuckGo<br/>via ddgs, no key"]
    end

    Normalize["3️⃣ Normalize<br/>→ one ResearchResult schema for all six"]
    Dedupe["4️⃣ Dedupe<br/>canonical URL + title-similarity (Jaccard ≥ 0.6)<br/>merges engagement, tracks also_seen_on"]
    Rank["5️⃣ Rank<br/>35% relevance + 25% recency + 15% source-quality<br/>+ 10% engagement + cross-source bonus"]
    Synth["6️⃣ Synthesize<br/>LLM call via run_step()<br/>citations built by CODE, never the LLM"]
    Package(["ResearchPackage:<br/>executive_summary, key_findings,<br/>interesting_angles, citations,<br/>source_coverage"])

    Query --> Selection
    Selection --> Fetch
    HN2 & RD2 & GH2 & PH2 & RSS2 & WEB2 --> Normalize
    Normalize --> Dedupe --> Rank --> Synth --> Package

    style Selection fill:#1e3a5f,color:#fff
    style Fetch fill:#1e3a5f,color:#fff
    style Synth fill:#0891b2,color:#fff
```

### The 6 sources

| Source | Auth | Cost | What it's good for |
|--------|------|------|---------------------|
| **Hacker News** | None | Free | Top/new/best/show/ask/job stories, optional top-comment context |
| **Reddit** | OAuth2 client-credentials | Free | Subreddit-aware search with keyword-based subreddit inference (AI → r/LocalLLaMA, r/MachineLearning; startups → r/startups, r/SaaS; falls back to sitewide search) |
| **GitHub** | Optional token (works unauthenticated) | Free | Repository discovery — stars, forks, language, topics, recent activity |
| **Product Hunt** | GraphQL v2 token | Free | Recently-launched products, filtered client-side by keyword since PH's API has no free-text search |
| **RSS/Atom** | None | Free | 8 built-in feeds (TechCrunch, VentureBeat, HN Best/Newest, Y Combinator, MIT Tech Review, Google AI Blog, Hugging Face) — fully overridable via `RSS_FEEDS` |
| **Web (DuckDuckGo)** | None | Free | General web search via the `ddgs` package — zero configuration |
| **X (Twitter)** | Composio, read-only | Paid API | **Optional only** — never chosen by the selection heuristic, reachable only via explicit `sources=["x", ...]` |

### Every source is isolated from every other

```python
async def _fetch_source(source: str, query: str, limit: int) -> list[ResearchResult]:
    adapter = ALL_SOURCES[source]
    try:
        return await adapter(query, limit)
    except Exception as exc:
        logger.warning("research_source_adapter_failed", source=source, error=str(exc))
        return []   # one dead source degrades gracefully — never sinks the run
```

Reddit's credentials aren't configured yet in most fresh setups? No problem — that source silently returns `[]`, gets logged, and the other five keep working. This was proven in production use during development: Product Hunt and web-search feeds both hit real transient failures (missing tokens, HTTP redirects) during testing, and neither ever took down a research run.

### Ranking is never "sort by popularity"

```python
score = (0.35 * relevance) + (0.25 * recency) + (0.15 * source_quality) + (0.10 * engagement) + cross_source_bonus
```

A viral-but-irrelevant Reddit thread loses to a quiet, on-topic RSS announcement from an official blog — relevance, recency, and source trust deliberately outweigh raw engagement 9-to-1.

---

## Safety & the Human-Approval Gate

```mermaid
sequenceDiagram
    participant Agent as Runtime Agent
    participant Guard as guardrails.py
    participant Registry as tools/registry.py
    participant Gate as approval_gate.py
    participant DB as Postgres
    participant Human as 👤 Human
    participant LinkedIn as LinkedIn (via Composio)

    Agent->>Guard: matches_refusal_topic(text)?
    alt sensitive topic matched
        Guard-->>Agent: topic name
        Agent->>Human: escalate_to_human() — no draft generated
    else clean
        Agent->>Agent: draft + self-score confidence
        alt confidence < 0.75
            Agent->>Human: escalate — needs_human_rewrite
        else confidence >= 0.75
            Agent->>Registry: execute_tool(name, args, approved=False)
            Registry-->>Registry: requires_approval? BLOCKED without approved=True
            Agent->>Gate: submit_for_approval(tool_name, arguments, reason, confidence)
            Gate->>DB: INSERT pending approval request
            Gate-->>Agent: {status: "pending", id}
            Human->>Gate: approve(approval_id) — the ONLY function<br/>allowed to call execute_tool(..., approved=True)
            Gate->>Registry: execute_tool(tool_name, args, approved=True)
            Registry->>LinkedIn: the real action finally happens
        end
    end
```

### The five refusal topics (regex-matched, `guardrails.py`)

| Topic | Example trigger |
|-------|------------------|
| Political endorsement | *"who should I vote for..."* |
| Health/financial/legal advice | *"what medication should I take"*, *"should I invest in..."* |
| Disparagement | *"bash our competitor"*, *"worst CEO"* |
| Engagement-bait / misinformation | *"comment yes if..."*, *"unverified claim"* |
| Impersonation | *"pretend to be..."*, *"ghostwrite this as..."* |

### Rate & cost caps

| Cap | Default | Enforced by |
|-----|---------|-------------|
| LLM spend | $10/day | `llmops/cost_tracker.py` |
| Posts | 3/day | `LINKEDIN_API_RATE_LIMIT_POSTS_DAILY` |
| Deletes | 3/day | `LINKEDIN_API_RATE_LIMIT_DELETES_DAILY` |
| Comment/DM replies | 20/day | `LINKEDIN_API_RATE_LIMIT_REPLIES_DAILY` |
| Connection requests | 5/day | `LINKEDIN_API_RATE_LIMIT_CONNECTIONS_DAILY` |
| Likes | 20/day | `LINKEDIN_API_RATE_LIMIT_LIKES_DAILY` |

Plus a kill switch (`safety/kill_switch.py`) that `approve()` checks before executing anything — flip it and every pending approval refuses to execute until it's cleared, system-wide.

A static audit enforces all of this stays true: `python -m app.safety.audit` walks the full tool registry and fails loud if it ever finds a risky tool without a gate.

---

## Tool Registry

19 tools total, 6 requiring human approval:

| Tool | Approval? | Purpose |
|------|:---:|---------|
| `search_knowledge_base` | — | Query the RAG index |
| `get_linkedin_notifications` | — | Poll comments/DMs/connection requests (read-only) |
| `draft_post` | — | Create a queued (unpublished) draft |
| `generate_analytics_report` | — | Summarize stored engagement data |
| `like_post` | — | Like a post (rate-capped, logged, not identity-risking) |
| `search_hackernews` | — | Hacker News research source |
| `search_reddit` | — | Reddit research source |
| `search_github` | — | GitHub research source |
| `search_producthunt` | — | Product Hunt research source |
| `search_rss` | — | RSS/Atom research source |
| `search_web` | — | Web search (DuckDuckGo by default) |
| `search_x_posts` | — | X research source (read-only, optional) |
| `save_research_note` | — | Persist a research finding (internal write only) |
| **`publish_post`** | ✅ | Publish to LinkedIn immediately |
| **`schedule_post`** | ✅ | Queue a future publish |
| **`delete_post`** | ✅ | Delete a published post — irreversible |
| **`reply_to_comment`** | ✅ | Public reply, attributed to the user |
| **`reply_to_dm`** | ✅ | Private message to a real third party |
| **`send_connection_request`** | ✅ | Connection request — ToS-sensitive |

Every tool has a Pydantic input schema (CLAUDE.md forbids a tool without one), every call is sandboxed with a timeout, and every execution is logged with inputs/outputs/latency/cost before the loop continues.

---

## Memory Architecture

```mermaid
flowchart LR
    subgraph Working["Working Memory — Redis"]
        W1["Current draft in progress"]
        W2["Notification thread being triaged"]
        W3["Session approval-queue state"]
    end
    subgraph Episodic["Episodic Memory — Postgres"]
        E1["12 months of posts + engagement stats"]
        E2["90 days of comment/DM threads<br/>+ resolution (auto/edited/escalated)"]
    end
    subgraph Semantic["Semantic Memory — FAISS"]
        S1["Brand voice / tone profile"]
        S2["Per-connection relationship context"]
        S3["Standing research interests"]
    end
    Note["Every write carries `source` + `confidence`<br/>— CLAUDE.md forbids untraceable memory"]
    Working -.-> Note
    Episodic -.-> Note
    Semantic -.-> Note
```

Retention: episodic post/engagement data kept 12 months then archived; DM/comment thread **content** (not metadata) purged after 90 days unless the user flags it important — LinkedIn ToS and privacy-driven, not arbitrary.

---

## RAG Pipeline

| Source | Type | Update Frequency | Chunking |
|--------|------|-------------------|----------|
| Past LinkedIn posts | Structured | On publish | 1 chunk/post |
| Brand voice / style guide | Document | Static | 500 tokens, semantic split |
| Industry news / trending topics | Document | Daily | 1 chunk/article |
| Comment/DM threads | Q&A pairs | Continuous | 1 chunk/thread |
| Research notes (multi-source) | Structured | Per research run | 1 chunk/note, deduped |

A single shared FAISS-backed `VectorStore` handles all five, idempotent by `(source_type, source_id)` — re-ingesting an edited document evicts its old chunks before adding fresh ones, so the index never silently accumulates duplicates.

---

## Eval Harness

```mermaid
flowchart TB
    Golden["📋 Golden Sets<br/>20 post cases + 15 reply cases<br/>(starter data — swap in your real curated<br/>50+30 set with zero code changes)"]
    Agents["Real agents:<br/>content_writer.write_post()<br/>engagement.handle_notification()"]
    Metrics["Deterministic metrics<br/>must_avoid_check · escalation_precision"]
    Judge["LLM-as-judge (via run_step)<br/>brand_voice_fidelity · groundedness · reply_appropriateness"]
    Baseline{{"baseline.json"}}
    Gate{"Δ > 5%?"}
    Pass(["✅ ship"])
    Fail(["❌ block — PRP regression policy"])

    Golden --> Agents
    Agents --> Metrics
    Agents --> Judge
    Metrics --> Baseline
    Judge --> Baseline
    Baseline --> Gate
    Gate -->|no| Pass
    Gate -->|yes| Fail

    style Fail fill:#dc2626,color:#fff
    style Pass fill:#16a34a,color:#fff
```

- **`must_avoid_check`** — deterministic phrase-ban check against `must_avoid` fields declared right in the golden set
- **`escalation_precision`** — recall (did it escalate when it should?) and precision (did it over-escalate?) computed purely from expected vs. actual behavior, no judge needed
- **LLM judge** — scores brand-voice fidelity and groundedness 1-5, bias-aware (no length bias, no position bias — one candidate scored at a time against fixed written criteria)
- **First run establishes the baseline** rather than failing outright — there's nothing to regress against yet
- 15 of the 15 reply cases are independently cross-checked against the *real* `guardrails.matches_refusal_topic()` regex, not just trusted to LLM judgment — a dedicated test enforces this stays true

Run it: `pytest backend/evals -v --tb=short`

---

## Self-Learning Loop

```mermaid
flowchart LR
    Signal1["👍 Approved"] & Signal2["👎 Rejected"] & Signal3["✏️ Edited"] & Signal4["📈 Engagement<br/>outcome (7-day lag)"] --> Capture["feedback.py<br/>capture_feedback()"]
    Capture --> DB[(Feedback table)]
    DB -->|"≥ 5 negative signals<br/>in the window"| Reflect["reflection_job.py<br/>run_reflection() via run_step()"]
    Reflect --> Classify{"change_type?"}
    Classify -->|retrieval_weight<br/>few_shot_example<br/>AND confidence ≥ 0.8| Auto["✅ auto_applied<br/>+ full audit log"]
    Classify -->|system_prompt<br/>brand_voice_profile<br/>new_tool<br/>approval_gating_rule<br/>confidence_threshold<br/>— ANY confidence| Review["⏸️ pending human review<br/>— NEVER auto-applies, unconditionally"]
    Classify -->|unrecognized type| Review
    Review --> Human2["👤 approve_proposal() / reject_proposal()"]

    style Auto fill:#16a34a,color:#fff
    style Review fill:#7c3aed,color:#fff
```

The hard line, verified by a parametrized test at **confidence = 1.0**: a `system_prompt` or `safety_threshold` proposal never auto-applies, no matter how sure the reflection job's own analysis claims to be. That's exactly the case a self-modifying system must never trust itself on.

| What can auto-apply | What always needs a human |
|---|---|
| Retrieval ranking weight tweaks | System prompt changes |
| Additive few-shot examples | Brand-voice profile changes |
| *(only at ≥ 0.8 confidence)* | New tools |
| | Approval-gating rule changes |
| | Confidence threshold changes |

---

## End-to-End Workflows

### Post creation, from idea to LinkedIn

```mermaid
sequenceDiagram
    autonumber
    participant RAG as RAG Index
    participant CS as Content Strategist
    participant CW as Content Writer
    participant Gate as Approval Gate
    participant Human as 👤 Human
    participant LI as LinkedIn

    CS->>RAG: retrieve(trending topics + last 30 days of posts)
    CS->>CS: dedupe against recently-covered topics
    CS->>CW: PostBrief {topic, angle, format, date}
    CW->>RAG: retrieve(brand voice + past posts + news)
    CW->>CW: draft + self-score confidence
    alt confidence < 0.75
        CW-->>Human: needs_human_rewrite (never queued)
    else confidence >= 0.75
        CW->>Gate: submit_for_approval(publish_post or schedule_post)
        Gate-->>Human: pending approval, full draft shown
        Human->>Gate: approve()
        Gate->>LI: publish/schedule — the only moment anything goes public
    end
```

### Multi-source research, in one call

```mermaid
sequenceDiagram
    autonumber
    participant Caller
    participant Pipeline as research_pipeline.research()
    participant HN as Hacker News
    participant GH as GitHub
    participant PH as Product Hunt
    participant RSS as RSS
    participant RD as Reddit

    Caller->>Pipeline: research("AI agents")
    par concurrent — one failure isolated
        Pipeline->>HN: fetch(query, limit)
        Pipeline->>GH: fetch(query, limit)
        Pipeline->>PH: fetch(query, limit)
        Pipeline->>RSS: fetch(query, limit)
        Pipeline->>RD: fetch(query, limit)
    end
    HN-->>Pipeline: results (or [] on failure)
    GH-->>Pipeline: results
    PH-->>Pipeline: results (or [] — e.g. missing token)
    RSS-->>Pipeline: results
    RD-->>Pipeline: results
    Pipeline->>Pipeline: dedupe (URL + title similarity)
    Pipeline->>Pipeline: rank (relevance/recency/quality/engagement)
    Pipeline-->>Caller: normalized, ranked list of dicts
```

### Weekly digest → delete suggestion (never auto-executed)

```mermaid
sequenceDiagram
    autonumber
    participant Cron as Weekly Trigger
    participant AA as Analytics Agent
    participant Tool as generate_analytics_report
    participant Gate as Approval Gate
    participant Human as 👤 Human

    Cron->>AA: generate_weekly_digest()
    AA->>Tool: execute_tool() — deterministic stats, no LLM
    AA->>AA: LLM judgment: which posts look stale/risky?
    alt post flagged
        AA->>Gate: suggest_deletion() — NO confidence param exists
        Gate-->>Human: full post content + publish date + engagement stats
        Note over Gate,Human: never a bare post ID.<br/>never skipped for "obviously fine."
        Human->>Gate: approve or reject
    end
```

---

## A Note on "Animations"

Being upfront: a plain `README.md` rendered on GitHub can't run real animation — no JS, no CSS transitions, no live motion. What it *can* do natively is render [Mermaid](https://mermaid.js.org/) diagrams, which is why this document leans on them heavily above rather than static ASCII art or (worse) a fabricated animated GIF I have no way to actually produce or verify renders correctly. Every diagram in this README is real, valid Mermaid syntax that GitHub will render as an interactive-feeling SVG in the actual repo view. If you want true animation (a recorded terminal session of the eval suite running, a GIF of the approval-queue UI once one exists), that's a good candidate for a `/docs/media` folder with real captured assets — nothing here should be treated as a placeholder for that.

---

## Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Orchestration | CrewAI-style role-based agents | Simpler fit than a conversational pattern for this workflow |
| Inference (primary) | Anthropic Claude | Hosted, strong generation quality |
| Inference (worker) | Hermes via vLLM (self-hosted, optional) | Cheap/fast for high-volume triage |
| RAG | FAISS | No KG layer for MVP |
| Serving | FastAPI (`app/main.py`) | Settings, approval queue, learning queue, cost, health |
| Frontend | React + Vite + TypeScript (`frontend/`) | One view per API resource, no framework/state-library overhead needed at this size |
| Scheduling | APScheduler | Weekly reflection-job trigger, wired into the app's startup lifespan |
| Persistence | Postgres + Redis + FAISS | Episodic / working / semantic split |
| LinkedIn integration | Composio | Auth, token refresh, low-level rate limits offloaded |
| X integration | Composio, read-only scope | Optional research source only |
| Web search | `ddgs` (DuckDuckGo) | No API key, swappable via `WebSearchProvider` interface |
| RSS parsing | `feedparser` | Handles RSS 2.0 / Atom / RDF dialect variance |
| Testing | pytest + pytest-asyncio + pytest-cov | 399 tests, 88.7% coverage |

---

## Project Structure

```
backend/
├── app/
│   ├── main.py               # FastAPI app — settings/approvals/learning-queue/cost/health
│   ├── agents/              # The 5 runtime agents + research_pipeline/sources/schema
│   ├── harness/              # run_step() — the sole LLM choke point, state machine, stopping conditions
│   ├── tools/                 # 19 registered tools + sandbox + rate limiting + Composio client
│   ├── memory/                # working / episodic / semantic stores
│   ├── rag/                   # ingestion + retrieval (shared FAISS VectorStore)
│   ├── context/                # token-budget assembly + compaction
│   ├── safety/                 # guardrails, approval gate, kill switch, cost cap, audit CLI
│   ├── llmops/                  # model router (+ live route_and_call), anthropic/hermes clients, tracer
│   ├── learning/                 # feedback capture, reflection job, proposal review queue, scheduler
│   └── models/                    # SQLAlchemy models (approvals, feedback, proposals, settings, episodes)
├── evals/                    # golden sets, metrics, LLM judge, regression-gate runner
└── tests/                    # 350+ tests for everything above

frontend/
├── src/
│   ├── api.ts                # typed fetch client for every backend endpoint
│   ├── types.ts               # response shapes, mirrors backend/app/main.py exactly
│   ├── ActorProvider.tsx       # persisted "acting as" identity for approve/reject calls
│   └── views/                   # ApprovalQueueView, LearningProposalsView, SettingsView, CostView
└── README.md                 # frontend-specific setup/build docs
```

---

## Model Routing & Cost Controls

Every `(agent, step)` pair resolves to exactly one tier — never hardcoded elsewhere, always through `llmops/model_router.route()`:

| Agent | Step | Tier | Why |
|-------|------|:---:|-----|
| Content Strategist | plan | Cheap | Planning/routing, not generation |
| Content Writer | draft | **Primary** | Generation quality matters |
| Engagement | triage | Worker | High-volume, low-stakes |
| Engagement | draft | **Primary** | Reply quality matters |
| Analytics | summarize | Cheap | Digest summarization |
| Research | triage | Worker | High-volume post filtering |
| Research | digest | **Primary** | Final write-up quality |
| Research | synthesize | **Primary** | Multi-source synthesis |
| Evals | judge | **Primary** | Gates ship/no-ship decisions |
| Learning | reflect | **Primary** | Infrequent, high-stakes proposals |

---

## Environment Variables

```env
# --- Core infra ---
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db
REDIS_URL=redis://localhost:6379/0
VECTOR_DB_PATH=./data/faiss_index
CREDENTIAL_ENCRYPTION_KEY=       # required before saving dashboard credentials
DASHBOARD_API_KEY=               # required for real deployments; enables X-Dashboard-API-Key auth

# --- Inference ---
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL_PRIMARY=claude-sonnet-5
ANTHROPIC_MODEL_CHEAP=claude-haiku-4-5
HERMES_ENDPOINT=http://localhost:8001/v1
LLM_COST_BUDGET_DAILY_USD=10

# --- Composio (LinkedIn required, X optional) ---
COMPOSIO_API_KEY=
COMPOSIO_LINKEDIN_CONNECTED_ACCOUNT_ID=
COMPOSIO_X_CONNECTED_ACCOUNT_ID=

# --- Rate caps ---
LINKEDIN_API_RATE_LIMIT_POSTS_DAILY=3
LINKEDIN_API_RATE_LIMIT_DELETES_DAILY=3
LINKEDIN_API_RATE_LIMIT_REPLIES_DAILY=20
LINKEDIN_API_RATE_LIMIT_CONNECTIONS_DAILY=5
LINKEDIN_API_RATE_LIMIT_LIKES_DAILY=20

# --- Research sources (all optional except what you want active) ---
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=ai-linkedin-manager-research-agent/1.0
GITHUB_TOKEN=                    # optional — unauthenticated works at a lower rate
PRODUCTHUNT_TOKEN=
WEB_SEARCH_PROVIDER=duckduckgo   # duckduckgo | brave | none — no key needed for duckduckgo
RSS_FEEDS=                       # leave unset to use the 8 built-in default feeds
```

See `.env.example` at the repo root for the complete, currently-accurate list. `python-dotenv` loads `.env` automatically — nothing else to wire up.

---

## Getting Started

```bash
git clone <this-repo-url>
cd AI_LinkedIn_Manager

cp .env.example .env
# fill in ANTHROPIC_API_KEY, COMPOSIO_*, and any research-source credentials you have

python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt

# run the full test suite from the repo root (no live credentials needed — everything runs on fakes)
python -m pytest backend/tests backend/evals -v

# run static checks and safety/tool audits
ruff check backend
mypy backend/app --ignore-missing-imports
PYTHONPATH=backend python -m app.tools.registry --validate-all-schemas
PYTHONPATH=backend python -m app.safety.audit

# run the API server
cd backend
uvicorn app.main:app --reload
# → http://localhost:8000/docs for interactive API docs (settings, approval
#   queue, learning-proposal queue, cost summary, health)
```

Hacker News, RSS, and DuckDuckGo web search work with **zero configuration**. Reddit, GitHub, and Product Hunt need the credentials above (GitHub works unauthenticated too, just at a lower rate limit). Without `ANTHROPIC_API_KEY` set, agents still run in tests (against fakes) but any endpoint that triggers a real model call (e.g. `/learning/reflect`) will fail loudly rather than pretend to succeed.

For a local production-like stack, use Docker Compose from the repo root:

```bash
docker compose up --build
```

For real database changes, use the Alembic scaffold in `backend/alembic/`; `Base.metadata.create_all()` remains as a local/test safety net, not the deployment migration strategy.

---

## Dashboard (Frontend)

A React + Vite + TypeScript single-page dashboard (`frontend/`) — one view per existing API resource, no component framework, no state-management library, no router. It's a review-and-decide tool, not a complex app, so `useState` and four view components cover it.

```mermaid
flowchart LR
    subgraph FE["frontend/ — Vite dev server, localhost:5173"]
        AQ["Approval Queue view"]
        SL["Self-Learning view"]
        ST["Settings view"]
        CO["Cost view"]
    end
    API["FastAPI — localhost:8000<br/>CORS-enabled for the dashboard's origin"]
    AQ & SL & ST & CO -->|fetch, JSON| API
    style API fill:#0891b2,color:#fff
```

| View | Backend resource | What you can do |
|------|-------------------|-------------------|
| **Workflows** | `/workflows/*` | Manually trigger research, content, analytics, and engagement runs |
| **Connections** | `/credentials/*` | Save API keys/tokens/connected-account IDs encrypted at rest |
| **Brand Voice** | `/brand-voice/*` | Maintain titled brand-voice profiles that are also ingested into RAG |
| **Approval Queue** | `/approvals/*` | See every pending gated action with full argument content shown (never a bare post ID) — approve or reject |
| **Self-Learning** | `/learning/proposals/*`, `/learning/reflect` | Review reflection-job proposals (flagged clearly when a type can never auto-apply), trigger an on-demand reflection run |
| **Settings** | `/settings/{key}` | View/edit agent settings like `research_agent.poll_interval` |
| **Cost** | `/cost` | Today's LLM spend vs. the daily cap, as a progress bar |

Every action carries a `decided_by` identity, set via the "Acting as" field in the header (persisted to `localStorage`) — the same audit trail `approval_gate.approve()`/`reject()` and `proposal_review.approve_proposal()`/`reject_proposal()` already record for every decision, human or otherwise.

```bash
cd frontend
npm install
npm run dev   # → http://localhost:5173
```

Requires the backend running separately (see [Getting Started](#getting-started)) with CORS allowing the dashboard's origin — `CORS_ALLOWED_ORIGINS` in `.env.example` already defaults to Vite's dev-server ports. If backend `DASHBOARD_API_KEY` is set, set matching `VITE_DASHBOARD_API_KEY` in `frontend/.env` so requests include `X-Dashboard-API-Key`. Full setup/build details: `frontend/README.md`.

---

## Testing & Validation Gates

```bash
# Gate 1 — foundation
pytest backend/tests/test_harness.py backend/tests/test_memory.py backend/tests/test_rag.py -v
python -m app.safety.audit

# Gate 2 — tools + safety
pytest backend/tests/test_tools.py backend/tests/test_safety.py -v

# Gate 3 — quality
pytest backend/evals -v --tb=short
python -m backend.evals.run_evals --compare-to-baseline

# Final gate
ruff check backend
mypy backend/app --ignore-missing-imports
pytest backend/tests backend/evals --cov=backend/app --cov=backend/evals --cov-fail-under=80
PYTHONPATH=backend python -m app.tools.registry --validate-all-schemas
PYTHONPATH=backend python -m app.safety.audit
cd frontend && npm run lint && npm run build
```

Current state: **497 tests passing, 88.33% coverage**, ruff/mypy clean, frontend lint/build clean, tool-registry audit green, safety audit green.

---

## Roadmap

**Done (this repo, right now):**
- [x] All 5 runtime agents, brand-voice/confidence-gated drafts
- [x] Human-approval flow blocking all 6 `requires_approval` tools, no bypass
- [x] Refusal-topic escalation across 5 categories
- [x] Multi-source Research Agent (6 sources, X optional)
- [x] Eval harness with regression gate
- [x] Self-learning loop with hard human-review lines
- [x] FastAPI serving layer (`main.py`) — settings, approval queue, learning-proposal queue, cost, health, all over the same tested infrastructure above
- [x] Self-learning loop running on an actual schedule (`learning/scheduler.py`, APScheduler, weekly by default, wired into the app's startup lifespan)
- [x] A real live LLM client (`model_router.route_and_call`) — Anthropic for primary/cheap tiers, Hermes/vLLM (OpenAI-compatible) for the worker tier, both via a forced structured tool-call so every existing agent's response-parsing contract is untouched
- [x] Dashboard UI for workflows, connections, brand voice, approval queue, self-learning queue, agent settings, and cost (`frontend/`, React + Vite)
- [x] Production hardening scaffold: encrypted credential store, optional dashboard API-key guard, Alembic baseline, Docker Compose, CI, and centralized Python tooling config

**Explicitly post-MVP (per the original spec):**
- [ ] Connection-relationship knowledge graph
- [ ] Analytics-driven auto-scheduling

**Known limitations carried into the live client:** `RouteAndCallResponse.tool_calls` is always `[]` — no runtime agent built in this codebase ever passes a non-`None` `tool_executor` to `run_step()` (agents call `tools.registry.execute_tool()` directly outside the harness loop), so harness-native tool execution/logging remains future work. `schedule_post` is registered and approval-gated but intentionally disabled by the Content Writer until a real scheduling backend exists. Anthropic pricing in `.env.example` is placeholder defaults, not verified real per-token pricing — override before trusting the cost cap for anything real.

---

## How This Was Built

This system was built PRP-style: a detailed spec (`INITIAL.md`) generated a full implementation blueprint, executed in phases with validation gates between each:

```
Phase 1 (Foundation) → Phase 2 (5 runtime agents + safety) → Phase 3 (evals + self-learning)
  → Phase 4 (FastAPI serving layer + scheduled learning loop) → Phase 5 (live LLM client)
```

Each phase's validation gate had to pass before the next started. Every non-trivial design decision in this codebase — why X is optional, why deletion has no confidence bypass, why `system_prompt` changes can never auto-apply — traces back to an explicit requirement in that original spec, not an implementation afterthought.
