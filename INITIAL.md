# INITIAL.md — Define Your Agent System

> Fill this out, then run `/generate-prp INITIAL.md`. This is the single source of truth for what the system does, who its runtime agents are, and what production guarantees it must meet.

---

## SYSTEM

**Name:** AI LinkedIn Manager

**Purpose:** Manages a professional's LinkedIn presence end-to-end — drafts on-brand posts, engages with the feed (likes/comments), drafts replies to comments and DMs on the user's behalf, tracks connection requests, and produces a weekly performance digest. It also researches AI/Agentic AI/tooling developments on X (Twitter) to keep post topics current. "Done" for a single interaction means: a draft (post, reply, or connection action) is generated, grounded in the user's brand voice, past content, and current research, and either auto-approved as safe informational output or queued for explicit human approval before anything touches LinkedIn publicly.

**Type:** Hybrid (multi-agent crew + deterministic automation) — CrewAI-style role-based agents for content/engagement decisions, n8n for scheduled publish triggers and polling.

---

## TECH STACK

| Layer | Choice |
|-------|--------|
| Orchestration | CrewAI (role-based crew: Strategist, Writer, Engagement, Analytics, Research — simpler fit than Autogen's conversational pattern for this workflow) |
| Inference (primary) | Hosted API (Anthropic Claude) |
| Inference (worker, optional) | Self-hosted (Hermes via vLLM) for low-risk bulk tasks: notification triage, engagement-priority scoring, X post triage/summarization |
| RAG | FAISS, no KG layer for MVP |
| Serving | FastAPI |
| Memory store | Postgres (episodic + structured) + Redis (working/session) + FAISS (semantic) |
| LinkedIn integration | Composio (managed LinkedIn tool actions: post, delete, comment, message, connect — auth + rate-limit handling offloaded to Composio) |
| X (Twitter) integration | Composio (read-only X search/timeline actions for research — same Composio account, separate connected-app scope; no posting/replying on X). Poll cadence defaults to daily, stored as a setting the user can change from the dashboard UI, not a hardcoded constant. |
| Automation glue | n8n — polls Composio for new notifications/DMs on a schedule, triggers publish/delete jobs for approved actions |

---

## RUNTIME AGENTS

### Agent 1: Content Strategist Agent
**Goal:** Decide what to post about — pulls from the content calendar, trending industry topics (RAG), and gaps in recent posting history; produces a topic + angle + target format (text/article/poll).
**Inputs:** Content calendar entries, RAG-retrieved trending topics, last 30 days of published posts (episodic memory)
**Outputs:** A structured post brief (topic, angle, format, target publish date) handed to the Writer Agent
**Model:** Hosted API, small/cheap tier — this is a planning/routing task, not generation

### Agent 2: Content Writer Agent
**Goal:** Write the full post copy in the user's brand voice, grounded in the brief from the Strategist and the user's style guide.
**Tools it can call:** `search_knowledge_base`, `draft_post`
**RAG sources it queries:** Brand voice/style guide, user's past posts (for tone/structure), industry news feed (for factual grounding)
**Escalation condition:** Brand-voice fidelity confidence < 0.75 → flag draft as "needs human rewrite" instead of presenting as ready-to-approve

### Agent 3: Engagement Agent
**Goal:** Monitor notifications (comments, DMs, connection requests) and draft appropriate replies or actions; scan the feed for posts worth liking/commenting on to build visibility.
**Tools it can call:** `get_linkedin_notifications`, `search_knowledge_base`, `like_post`, `reply_to_comment`, `reply_to_dm`, `send_connection_request`
**RAG sources it queries:** Past comment/DM threads, brand voice/style guide, connection relationship notes (semantic memory)
**Escalation condition:** Any DM/comment involving a sensitive topic (see refusal topics below), or reply confidence < 0.75 → escalate to human, do not draft

### Agent 4: Analytics & Reporting Agent
**Goal:** Produce a weekly digest of post performance (impressions, engagement rate, follower delta), flag underperforming content patterns, and suggest deletion of posts that are stale or reputationally risky.
**Tools it can call:** `generate_analytics_report`, `search_knowledge_base`, `delete_post`
**RAG sources it queries:** Past posts + their engagement stats (episodic memory)
**Escalation condition:** A `delete_post` suggestion is never auto-executed — always routed to the approval queue with the post content and reason attached, regardless of confidence

### Agent 5: Research Agent
**Goal:** Track X (Twitter) for developments in AI, Agentic AI, Hermes (the self-hosted model this project uses), and AI tooling; produce digestible research notes that feed the Content Strategist's trending-topics input and give the user an early read on what's changing in their space.
**Tools it can call:** `search_x_posts`, `save_research_note`, `search_knowledge_base`
**RAG sources it queries/writes:** Writes to the X/Twitter research feed (new RAG source below); reads industry news feed to avoid duplicate coverage
**Model:** Hermes worker tier for high-volume triage/summarization of X posts; escalates to hosted API primary tier only for producing the final digest write-up
**Poll interval:** Default **daily**, not hourly. Stored as a user-editable setting (not hardcoded), exposed later in the observability dashboard UI so the user can change the cadence without a code change or redeploy.
**Escalation condition:** None — read-only research, informational output only. Never posts, replies, likes, or DMs on X; X access is search/read only via Composio.

---

## TOOLS

> All tools that touch LinkedIn (`get_linkedin_notifications`, `like_post`, `publish_post`, `schedule_post`, `delete_post`, `reply_to_comment`, `reply_to_dm`, `send_connection_request`) are implemented as thin wrappers around **Composio** LinkedIn actions — Composio owns OAuth, token refresh, and low-level rate limiting; our `requires_approval` gate and rate caps sit on top of it, not instead of it. `search_x_posts` is likewise a Composio-backed X (Twitter) action, scoped read-only — this project has no tool that posts, replies, or DMs on X.

| Tool | Purpose | Requires human approval? |
|------|---------|---------------------------|
| `search_knowledge_base` | Query RAG index (brand voice, past posts, news, threads, X research) | No |
| `get_linkedin_notifications` | Poll Composio for comments/DMs/connection requests (read-only) | No |
| `draft_post` | Create a queued draft post (not published) | No |
| `generate_analytics_report` | Summarize performance from stored engagement data | No |
| `search_x_posts` | Search X (Twitter) via Composio for AI/Agentic AI/Hermes/tooling updates (read-only) | No |
| `save_research_note` | Persist a research finding to semantic memory / RAG index for reuse by the Strategist Agent | No (internal write only, not published anywhere) |
| `like_post` | Like a post as the user, rate-capped, via Composio | No (logged + rate-capped, not identity-risking) |
| `publish_post` | Publish a post immediately to LinkedIn, via Composio | Yes — public, external, irreversible |
| `schedule_post` | Queue a post to auto-publish at a future time, via Composio + n8n | Yes — commits to a future public action |
| `delete_post` | Delete a previously published post, via Composio | Yes — irreversible, public, requires explicit confirmation of which post |
| `reply_to_comment` | Post a public reply to a comment on the user's post, via Composio | Yes — public, attributed to the user |
| `reply_to_dm` | Send a private message to a real third party, via Composio | Yes — external, contacts a real person |
| `send_connection_request` | Send a connection request to another LinkedIn user, via Composio | Yes — external, contacts a real person, ToS-sensitive |

---

## MEMORY REQUIREMENTS

**Working memory:** Current draft being composed, current notification thread being triaged, current approval queue state for the session.

**Episodic memory:** Last 12 months of published posts with engagement stats; last 90 days of comment/DM threads and how they were resolved (auto-approved, human-edited, escalated).

**Semantic memory:** User's brand voice/tone profile, topics the user cares about, per-connection relationship context (e.g. "recruiter," "client," "do not auto-engage"), and standing research interests (AI, Agentic AI, Hermes, tooling) that steer the Research Agent's X search terms.

**Retention:** Episodic post/engagement data retained 12 months then archived to cold storage. DM/comment thread *content* (not metadata) purged after 90 days unless flagged important by the user, per LinkedIn ToS and user privacy expectations. All memory writes must carry `source` and `confidence` per CLAUDE.md.

---

## RAG SOURCES

| Source | Type | Update frequency | Chunking strategy |
|--------|------|-------------------|--------------------|
| User's past LinkedIn posts | Structured | Continuous (on publish) | 1 chunk per post |
| Brand voice / style guide doc | Document | Static / on-upload | 500 tokens, semantic split |
| Industry news / trending topics feed | Document (RSS/API) | Daily | 1 chunk per article |
| Past comment/DM threads | Structured (Q&A pairs) | Continuous | 1 chunk per thread |
| X (Twitter) AI/Agentic AI/Hermes/tooling research notes | Structured (research notes, via Composio) | Daily by default — poll interval is a user-editable setting, changeable from the UI without redeploying | 1 chunk per research note (deduped/summarized from source posts) |

**Knowledge Graph layer needed?** No for MVP. Post-MVP candidate: connection relationship graph (Person → Company → Relationship type) to improve engagement targeting.

---

## SAFETY & APPROVAL REQUIREMENTS

- [x] Actions that mutate external systems require approval: `publish_post`, `schedule_post`, `delete_post`, `reply_to_comment`, `reply_to_dm`, `send_connection_request`
- [x] Confidence threshold below which the system must escalate rather than answer: 0.75 (brand-voice fidelity for drafts, reply-appropriateness for engagement). `delete_post` always escalates regardless of confidence — deletion is irreversible.
- [x] Topics/requests the system must always refuse or redirect: political endorsements, health/financial/legal advice, content disparaging a named individual or competitor, engagement-bait/misinformation, anything impersonating the user in a way that misrepresents authorship
- [x] Rate/cost caps: max $10/day LLM spend; max 5 connection requests/day; max 20 comment/DM replies/day; max 3 posts/day; max 3 delete actions/day (LinkedIn ToS + Composio rate limits)
- [x] `delete_post` approval prompt must show the full post content, publish date, and engagement stats before a human can confirm — never a bare post ID

---

## EVALUATION CRITERIA

**Golden test set source:** 50 curated (topic → ideal post) pairs plus 30 comment/DM scenarios with ideal replies, curated by the user from their actual posting history and past correspondence.

**Metrics that matter:**
- [x] Brand-voice fidelity (LLM-as-judge scoring against style guide)
- [x] Groundedness (any cited stat/news in a post is supported by a retrieved source, not hallucinated)
- [x] Escalation precision (escalates when it should — sensitive topics, low confidence — without over-escalating routine replies)

**Regression policy:** No eval score may drop more than 5% between versions without explicit user sign-off.

---

## SELF-LEARNING SCOPE

**Feedback signals available:** User approve/reject/edit actions on drafts; actual engagement metrics (likes/comments/shares/impressions) measured 7 days post-publish.

**What improves automatically:** Retrieval ranking weights in the RAG index; few-shot examples pulled from top-performing past posts.

**What requires human review before deploying:** Any change to the system prompt or brand-voice profile; any new tool; any change to approval-gating rules or confidence thresholds.

---

## MVP SCOPE

Must Have:
- [ ] Content Strategist + Writer agents produce brand-voice-grounded post drafts
- [ ] Engagement Agent drafts comment/DM replies and connection-request suggestions for approval
- [ ] Research Agent surfaces AI/Agentic AI/Hermes/tooling updates from X, feeding the Strategist Agent's topic selection, polling daily by default
- [ ] Research Agent's X poll interval is a stored, API-backed setting (default: daily) — never a hardcoded constant, so it can be exposed in a UI later without a code change
- [ ] Human-approval flow blocks all `requires_approval` tools with no bypass
- [ ] Escalation path to a human when confidence < 0.75 or a refusal topic is detected

Post-MVP:
- [ ] Dashboard UI control to view/change the Research Agent's poll interval (and other agent-cadence settings) without a redeploy
- [ ] Self-learning loop active (auto-tuned retrieval weights, few-shot examples)
- [ ] Knowledge graph layer for connection relationships
- [ ] Analytics-driven auto-scheduling optimization

---

## ACCEPTANCE CRITERIA

- [ ] Agent produces brand-voice-grounded drafts matching the golden set (target: 85% LLM-judge approval)
- [ ] Agent escalates instead of guessing when confidence is below 0.75, or on any refusal-topic match
- [ ] No `requires_approval` tool executes without explicit approval, verified by test
- [ ] `delete_post` cannot execute without the human seeing the full post content in the approval prompt, verified by test
- [ ] Research Agent has no tool capable of posting/replying/DMing on X, verified by test (tool registry contains read-only X actions only)
- [ ] Full trace (tokens, cost, latency) exists for every request
- [ ] System stays within the $10/day cost cap and rate caps under load test

---

## FORBIDDEN

- Must never auto-publish, auto-schedule, auto-delete, auto-reply, or auto-send a connection request without explicit human approval — no exceptions
- Must never delete a post without the human seeing the exact post content and confirming that specific post (no batch/bulk delete, no "clean up old posts" auto-approval)
- Must never mass-solicit connections or send bulk unsolicited outreach (violates LinkedIn ToS and this project's rate caps)
- Must never scrape or store third-party personal data beyond what's needed for reply context, and never beyond the 90-day retention window
- Must never generate content that disparages a named individual/competitor, gives health/financial/legal advice, or takes a political stance
- Must never bypass Composio for LinkedIn access (no direct scraping/unofficial API calls) — Composio is the sole integration path so auth, consent, and rate limits stay centrally enforced
- Must never post, reply, like, retweet, or DM on X (Twitter) — the Research Agent's X access is read-only search, full stop; posting on X is out of scope for this system

---

## RUN

```bash
/generate-prp INITIAL.md
/execute-prp PRPs/[name]-prp.md
```
