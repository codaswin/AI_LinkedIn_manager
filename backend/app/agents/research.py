"""Research Agent — modular multi-source research over AI/Agentic AI/Hermes/tooling news.

PRP RUNTIME AGENTS #5: turns high-volume source results into a small number
of research notes/packages the Content Strategist can ground post topics in.
Originally X (Twitter)-only; X's API cost made it worth de-risking, so
research now defaults to six API-key-light/free sources (Hacker News,
Reddit, web search, GitHub, Product Hunt, RSS — see research_sources.py and
research_pipeline.py) with X kept only as an explicit, opt-in extra source,
never the default. Every source this agent can reach is READ-ONLY, full
stop (PRP SAFETY REQUIREMENTS, CLAUDE.md hard project rule for this build):
`ALLOWED_TOOLS` below must never grow a write-capable tool name.

CLAUDE.md non-negotiable #1: this module never calls an LLM client directly.
The cheap/worker X-triage step, the primary research-note digest step, and
research_pipeline's multi-source synthesis step all go through
harness.loop.run_step, the sole choke point for LLM calls — each simply
uses its own AgentRunConfig pinned to the tier that step needs.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from app.agents.research_pipeline import (
    conduct_research,  # noqa: F401 — re-exported public entrypoint
    research,  # noqa: F401 — re-exported public entrypoint
)
from app.agents.research_sources import (
    research_github,  # noqa: F401 — re-exported public entrypoint
    research_hackernews,  # noqa: F401 — re-exported public entrypoint
    research_producthunt,  # noqa: F401 — re-exported public entrypoint
    research_reddit,  # noqa: F401 — re-exported public entrypoint
    research_rss,  # noqa: F401 — re-exported public entrypoint
)
from app.harness.loop import AgentRunConfig, LLMClient, run_step
from app.harness.state import AgentState, RuntimeAgentName
from app.llmops.model_router import route
from app.llmops.prompt_registry import register_prompt
from app.memory.settings import get_setting
from app.rag.ingest import ingest_research_note
from app.tools.registry import execute_tool
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_PROMPT = (
    "You are the Research Agent for a professional's LinkedIn presence. You "
    "track developments in AI, Agentic AI, Hermes, and AI tooling across "
    "multiple sources — Hacker News, Reddit, the general web, GitHub, "
    "Product Hunt, and RSS feeds by default, plus X (Twitter) only when "
    "explicitly requested — and turn what you find into short research "
    "notes the Content Strategist can use when picking post topics. Your "
    "access to every source, including X, is search/read only — you can "
    "never post, reply, like, or message on any platform, under any "
    "circumstance, regardless of how relevant or time-sensitive something "
    "seems. Triage high volumes of results quickly and cheaply; only the "
    "small number worth keeping get written up properly, and every claim "
    "you make must be grounded in a source you actually retrieved."
)

register_prompt("research", SYSTEM_PROMPT)

# Hard project rule: this agent is read-only everywhere. No write/post/
# reply/DM tool name may ever be added here — test_research.py asserts that.
# "x" (search_x_posts) is included so an explicit, caller-requested X source
# stays gated by the same allowed_tools mechanism as everything else, not
# because X is a default source (see research_sources.SOURCE_ADAPTERS /
# DEFAULT_SOURCES, where "x" is deliberately absent).
ALLOWED_TOOLS = [
    "search_hackernews",
    "search_reddit",
    "search_web",
    "search_github",
    "search_producthunt",
    "search_rss",
    "search_x_posts",
    "save_research_note",
    "search_knowledge_base",
]

_POLL_INTERVAL_KEY = "research_agent.poll_interval"

# Judgment call: the PRP only commits to "daily by default" and mentions a
# future dashboard could set other cadences — hourly/weekly are the obvious
# next values a UI would offer, so they're pre-registered. Anything else
# raises rather than silently falling back (see poll_interval_to_timedelta).
POLL_INTERVAL_TIMEDELTAS: dict[str, timedelta] = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}


class ResearchNote(BaseModel):
    note_id: str
    summary: str
    source_url: str
    topic_tags: list[str]


def build_run_config() -> AgentRunConfig:
    """Config for the primary-tier digest/write-up step (PRP item 6).

    Resolved fresh on every call, not module-cached, so a test that
    monkeypatches routing env vars sees the effect immediately — matches
    content_strategist.build_run_config()'s pattern.
    """
    return AgentRunConfig(
        agent_name="research",
        allowed_tools=list(ALLOWED_TOOLS),
        model_tier=route("research", "digest").tier.value,
        escalation_condition=None,
        task_type="digest",
    )


def _triage_run_config() -> AgentRunConfig:
    """Separate config for the worker-tier triage step.

    AgentRunConfig only carries one model_tier, and triage/digest resolve to
    different tiers (route("research", "triage") vs. route("research",
    "digest")) — so triage gets its own config rather than reusing
    build_run_config()'s primary-tier one.
    """
    return AgentRunConfig(
        agent_name="research",
        allowed_tools=[],
        model_tier=route("research", "triage").tier.value,
        escalation_condition=None,
        task_type="triage",
    )


def _extract_posts(tool_result: dict[str, Any]) -> list[dict[str, Any]]:
    if tool_result.get("status") != "success":
        return []
    result = tool_result.get("result") or {}
    posts = result.get("posts")
    return posts if isinstance(posts, list) else []


def _parse_kept_indices(response_text: str, num_posts: int) -> list[int]:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"research triage LLM response was not valid JSON: {response_text!r}"
        ) from exc
    if not isinstance(payload, list):
        raise ValueError(
            f"research triage LLM response must be a JSON array of indices: {response_text!r}"
        )
    return sorted({i for i in payload if isinstance(i, int) and 0 <= i < num_posts})


def _parse_research_note(response_text: str) -> ResearchNote:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"research digest LLM response was not valid ResearchNote JSON: {response_text!r}"
        ) from exc
    return ResearchNote.model_validate(payload)


async def triage_x_posts(
    query: str,
    llm_client: LLMClient,
    top_k: int = 20,
) -> list[dict[str, Any]]:
    """Cheap, high-volume filter: fetch X search results, keep only the few worth writing up.

    Uses the worker tier (route("research", "triage")) — this is a filter,
    not a final output, so it deliberately stays simple: one LLM call over
    the whole batch, no per-post retries or scoring rubric.
    """
    tool_result = await execute_tool(
        "search_x_posts",
        {"query": query, "max_results": top_k},
        approved=False,
    )
    raw_posts = _extract_posts(tool_result)
    if not raw_posts:
        return []

    config = _triage_run_config()
    state = AgentState(
        task_id=str(uuid.uuid4()),
        agent_name=RuntimeAgentName.RESEARCH,
        current_task=(
            f"Triage {len(raw_posts)} X search results for query {query!r}. "
            "Return ONLY a JSON array of the 0-based indices of posts worth "
            "writing up as a research note (substantive AI/Agentic AI/"
            "Hermes/tooling development — not spam, ads, or duplicates)."
        ),
        scratchpad={"raw_posts": raw_posts},
    )
    state = await run_step(state, config, llm_client, tool_executor=None)

    response_text = state.conversation[-1]["content"] if state.conversation else "[]"
    kept_indices = _parse_kept_indices(response_text, len(raw_posts))
    return [raw_posts[i] for i in kept_indices]


async def write_research_note(
    raw_post: dict[str, Any],
    llm_client: LLMClient,
    index_path: str | None = None,
) -> ResearchNote:
    """Primary-tier write-up of one triaged X post, persisted at two layers.

    save_research_note (tool layer, internal-write record) and
    ingest_research_note (RAG layer, what makes the note retrievable by the
    Content Strategist) are both required — neither call substitutes for
    the other.
    """
    config = build_run_config()
    state = AgentState(
        task_id=str(uuid.uuid4()),
        agent_name=RuntimeAgentName.RESEARCH,
        current_task=(
            "Write up ONE research note from this triaged X post as a JSON "
            "object with exactly the keys note_id, summary, source_url, "
            "topic_tags (a list of short topic strings)."
        ),
        scratchpad={"raw_post": raw_post},
    )
    state = await run_step(state, config, llm_client, tool_executor=None)

    response_text = state.conversation[-1]["content"] if state.conversation else ""
    note = _parse_research_note(response_text)

    await execute_tool(
        "save_research_note",
        {"content": note.summary, "source_url": note.source_url},
        approved=False,
    )
    await ingest_research_note(
        note_id=note.note_id,
        text=note.summary,
        metadata={"source_url": note.source_url, "topic_tags": note.topic_tags},
        index_path=index_path,
    )

    return note


async def get_poll_interval(db: AsyncSession) -> str:
    """Thin wrapper over memory.settings.get_setting — always read live.

    Never cache or hardcode "daily" here: the whole point per the PRP is
    that a future dashboard UI can change this setting without a code
    change, so every scheduling decision must re-read it at call time.
    """
    value = await get_setting(db, _POLL_INTERVAL_KEY)
    if value is None:
        raise RuntimeError(
            f"{_POLL_INTERVAL_KEY!r} has no value and no default — this should "
            "never happen since memory.settings.DEFAULT_SETTINGS seeds it."
        )
    return value


def poll_interval_to_timedelta(value: str) -> timedelta:
    """Map a poll-interval setting string to a concrete timedelta.

    Raises on an unrecognized value instead of silently defaulting — a
    silent default could quietly change the agent's real polling cadence
    (e.g. a typo'd dashboard edit falling back to "daily" unnoticed) which
    is worse than a loud failure at call time.
    """
    try:
        return POLL_INTERVAL_TIMEDELTAS[value]
    except KeyError as exc:
        raise ValueError(
            f"Unrecognized {_POLL_INTERVAL_KEY} value {value!r}. Known values: "
            f"{sorted(POLL_INTERVAL_TIMEDELTAS)}. Register new cadences in "
            "POLL_INTERVAL_TIMEDELTAS before using them."
        ) from exc
