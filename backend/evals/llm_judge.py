"""LLM-as-judge for brand-voice fidelity and groundedness.

CLAUDE.md non-negotiable #1: this module never calls an LLM client directly
— both judge_post() and judge_reply() go through harness.loop.run_step(),
exactly like every runtime agent's own LLM calls. RuntimeAgentName.EVALS
(harness/state.py) exists specifically so this module can build a valid
AgentState without misusing one of the 5 runtime agents' identities.

Bias note (skills/EVALS.md): the judge is instructed not to let response
length alone drive the score, and it never sees a competing response to
compare against (no position bias possible — one candidate is scored at a
time, against fixed written criteria).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.harness.loop import AgentRunConfig, LLMClient, run_step
from app.harness.state import AgentState, RuntimeAgentName
from app.llmops.model_router import route
from app.llmops.prompt_registry import register_prompt

JUDGE_SYSTEM_PROMPT = (
    "You are an impartial judge evaluating AI-generated LinkedIn content for a professional's "
    "account. Score strictly on the criteria given for each request — never let response length "
    "alone drive a score, and never favor a response just because it sounds more thorough or "
    "confident. Respond with only the requested JSON object, nothing else."
)

register_prompt("evals", JUDGE_SYSTEM_PROMPT)


def _judge_config() -> AgentRunConfig:
    """Resolved fresh on every call, matching every other agent's

    build_run_config() in this codebase.
    """
    return AgentRunConfig(
        agent_name="evals",
        allowed_tools=[],
        model_tier=route("evals", "judge").tier.value,
        escalation_condition=None,
        task_type="judge",
    )


def _parse_judge_json(response_text: str) -> dict[str, Any]:
    if not response_text:
        raise ValueError("eval judge returned an empty response")
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"eval judge response was not valid JSON: {response_text!r}") from exc
    if not isinstance(payload, dict):
        # ValueError (not TypeError) to match every other "malformed LLM JSON
        # output" raise in this codebase (research.py, research_pipeline.py,
        # content_strategist.py, analytics.py).
        raise ValueError(f"eval judge response must be a JSON object: {response_text!r}")  # noqa: TRY004
    return payload


async def judge_post(case: dict[str, Any], post_content: str, llm_client: LLMClient) -> dict[str, Any]:
    """Scores a Content Writer draft 1-5 on brand_voice_fidelity and groundedness.

    No retrieved-context/reference text is required — same self-scoring
    shape content_writer.py itself already uses (score confidence without a
    ground-truth answer), which is the right fit here since the golden set
    intentionally has no single "correct" post for a topic.
    """
    state = AgentState(
        task_id=f"judge-{case['id']}-{uuid.uuid4()}",
        agent_name=RuntimeAgentName.EVALS,
        current_task=(
            "Score this LinkedIn post 1-5 on brand_voice_fidelity (matches the given angle and "
            "style notes; not generic AI-blog tone) and groundedness (no fabricated statistics or "
            "unverifiable claims). Respond as JSON with exactly the keys brand_voice_fidelity "
            "(1-5), groundedness (1-5), and reasoning (one sentence)."
        ),
        scratchpad={
            "topic": case["topic"],
            "angle": case["angle"],
            "must_avoid": case.get("must_avoid", []),
            "post_content": post_content,
        },
    )
    state = await run_step(state, _judge_config(), llm_client, tool_executor=None)
    response_text = state.conversation[-1]["content"] if state.conversation else ""
    return _parse_judge_json(response_text)


async def judge_reply(case: dict[str, Any], draft_text: str, llm_client: LLMClient) -> dict[str, Any]:
    """Scores a drafted (non-escalated) Engagement reply 1-5 on

    reply_appropriateness and brand_voice_fidelity. Only meaningful for
    cases the agent actually drafted — an escalated case has no draft text
    to judge.
    """
    notification = case["notification"]
    state = AgentState(
        task_id=f"judge-{case['id']}-{uuid.uuid4()}",
        agent_name=RuntimeAgentName.EVALS,
        current_task=(
            "Score this drafted LinkedIn reply 1-5 on reply_appropriateness (directly and "
            "professionally addresses the original message) and brand_voice_fidelity (not "
            "generic AI-blog tone). Respond as JSON with exactly the keys reply_appropriateness "
            "(1-5), brand_voice_fidelity (1-5), and reasoning (one sentence)."
        ),
        scratchpad={
            "notification_type": notification.get("type"),
            "original_text": notification.get("text"),
            "draft_reply": draft_text,
        },
    )
    state = await run_step(state, _judge_config(), llm_client, tool_executor=None)
    response_text = state.conversation[-1]["content"] if state.conversation else ""
    return _parse_judge_json(response_text)
