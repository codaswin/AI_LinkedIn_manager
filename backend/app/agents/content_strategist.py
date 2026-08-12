"""Content Strategist Agent — decides WHAT to post about next.

PRP § RUNTIME AGENTS, "1. Content Strategist Agent": this agent's only
output is a structured PostBrief handed to the Content Writer Agent. It
never produces post copy itself, and its output is never user-facing, so
(per the PRP's Escalation column for this agent) it has no escalation
condition.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Literal

from app.harness.loop import AgentRunConfig, LLMClient, run_step
from app.harness.state import AgentState, RuntimeAgentName
from app.llmops.model_router import route
from app.llmops.prompt_registry import register_prompt
from app.rag.retrieve import retrieve
from pydantic import BaseModel

SYSTEM_PROMPT = (
    "You are the Content Strategist for a professional's LinkedIn presence. "
    "Your only job is deciding WHAT to post about next — never write the post "
    "itself. Given the content calendar, recent trending-topic research "
    "(including the Research Agent's X findings), and the last 30 days of "
    "published posts, produce ONE structured brief: topic, angle, target "
    "format (text/article/poll), and target publish date. Favor topics not "
    "already covered in the last 30 days. Ground the angle in retrieved "
    "research rather than generic commentary. Output only the brief — never "
    "post copy, that's the Content Writer's job."
)

register_prompt("content_strategist", SYSTEM_PROMPT)


class PostBrief(BaseModel):
    topic: str
    angle: str
    format: Literal["text", "article", "poll"]
    target_publish_date: date | None = None


def build_run_config() -> AgentRunConfig:
    """The PRP pins this agent to the cheap tier and search_knowledge_base only.

    Resolved fresh (not module-cached) so a test that monkeypatches routing
    env vars sees the effect immediately.
    """
    return AgentRunConfig(
        agent_name="content_strategist",
        allowed_tools=["search_knowledge_base"],
        model_tier=route("content_strategist", "plan").tier.value,
        escalation_condition=None,
    )


_WORD_RE = re.compile(r"[a-z0-9]+")
_OVERLAP_THRESHOLD = 0.4


def _keywords(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _overlaps_recent_topics(candidate_text: str, recent_post_topics: list[str]) -> bool:
    candidate_kw = _keywords(candidate_text)
    if not candidate_kw:
        return False
    for topic in recent_post_topics:
        topic_kw = _keywords(topic)
        if not topic_kw:
            continue
        overlap_ratio = len(candidate_kw & topic_kw) / len(topic_kw)
        if overlap_ratio >= _OVERLAP_THRESHOLD:
            return True
    return False


def _dedupe_against_recent_posts(
    hits: list[dict[str, Any]], recent_post_topics: list[str]
) -> list[dict[str, Any]]:
    """Prefer topics not covered in the last 30 days.

    Hits that substantially overlap an already-covered topic are excluded
    outright; if every retrieved hit overlaps (i.e. nothing fresh came back),
    fall back to the full, unfiltered set rather than handing the LLM an
    empty grounding context — deprioritized-but-present beats nothing.
    """
    fresh = [hit for hit in hits if not _overlaps_recent_topics(str(hit.get("text", "")), recent_post_topics)]
    return fresh if fresh else hits


def _parse_brief(response_text: str) -> PostBrief:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"content_strategist LLM response was not a valid PostBrief JSON object: {response_text!r}"
        ) from exc
    return PostBrief.model_validate(payload)


async def build_post_brief(
    recent_post_topics: list[str],
    calendar_entries: list[str],
    llm_client: LLMClient,
    index_path: str | None = None,
) -> PostBrief:
    query = " ".join(calendar_entries) if calendar_entries else "AI LinkedIn content trends"
    hits = await retrieve(
        query=query,
        source_types=["industry_news", "x_research_notes"],
        top_k=5,
        index_path=index_path,
    )
    grounding_context = _dedupe_against_recent_posts(hits, recent_post_topics)

    config = build_run_config()
    state = AgentState(
        task_id="content-strategist-plan",
        agent_name=RuntimeAgentName.CONTENT_STRATEGIST,
        current_task=(
            "Produce exactly one PostBrief as a JSON object with keys "
            "topic, angle, format (text/article/poll), and "
            "target_publish_date (ISO date or null), for the next LinkedIn post."
        ),
        scratchpad={
            "calendar_entries": calendar_entries,
            "recent_post_topics": recent_post_topics,
            "grounding_context": grounding_context,
        },
    )

    state = await run_step(state, config, llm_client, tool_executor=None)

    response_text = state.conversation[-1]["content"] if state.conversation else ""
    return _parse_brief(response_text)
