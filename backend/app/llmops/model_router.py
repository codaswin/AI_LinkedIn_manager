"""Model routing table for the AI LinkedIn Manager's 5 runtime agents.

This is the ONLY file in the codebase permitted to reference a specific
Anthropic/Hermes model name as a literal string — see CLAUDE.md's Forbidden
list: "Hardcoded API keys/model names in code — env vars + llmops/model_router.py".
Every other module must obtain a model identifier by calling route(); it must
never construct or hardcode one itself.

route() is called BY the harness's run_step() — it never calls an LLM API on
its own initiative, and no tool or agent may call an LLM directly, bypassing
the harness (CLAUDE.md non-negotiable #1).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class ModelTier(str, Enum):
    """Which inference tier a runtime-agent step is routed to."""

    PRIMARY = "primary"
    CHEAP = "cheap"
    WORKER = "worker"


class ModelProvider(str, Enum):
    ANTHROPIC = "anthropic"
    HERMES = "hermes"


# Fallback model identifiers, used only when the corresponding env var is
# unset. These are literal model-name strings by design (see module
# docstring) — do not copy them into any other file.
_DEFAULT_ANTHROPIC_MODEL_PRIMARY = "claude-sonnet-5"
_DEFAULT_ANTHROPIC_MODEL_CHEAP = "claude-haiku-4-5"
_DEFAULT_HERMES_MODEL = "hermes-3"
_DEFAULT_HERMES_ENDPOINT = "http://localhost:8001/v1"


@dataclass(frozen=True)
class ModelConfig:
    """Resolved routing decision for one (agent, step) pair."""

    tier: ModelTier
    provider: ModelProvider
    model: str
    endpoint: str | None = None


# (agent_name, step_name) -> tier.
#
# Engagement and Research each have two entries because they use two
# different tiers depending on which sub-step is running (cheap/worker
# triage vs. primary-tier final output) — routing is per-step, not per-agent,
# so the harness must pass the specific step name on every call.
_ROUTING_TABLE: dict[tuple[str, str], ModelTier] = {
    # Content Strategist Agent — planning/brief authoring (small/cheap tier).
    ("content_strategist", "plan"): ModelTier.CHEAP,
    # Content Writer Agent — full post drafting (primary tier, generation quality matters).
    ("content_writer", "draft"): ModelTier.PRIMARY,
    # Engagement Agent — notification triage/priority-scoring (self-hosted worker tier).
    ("engagement", "triage"): ModelTier.WORKER,
    # Engagement Agent — reply-drafting (primary tier).
    ("engagement", "draft"): ModelTier.PRIMARY,
    # Analytics & Reporting Agent — weekly digest summarization (small/cheap tier).
    ("analytics", "summarize"): ModelTier.CHEAP,
    # Research Agent — high-volume X-post triage/summarization (worker tier).
    ("research", "triage"): ModelTier.WORKER,
    # Research Agent — final research-note/digest write-up (primary tier).
    ("research", "digest"): ModelTier.PRIMARY,
    # Research Agent — multi-source synthesis into one ResearchPackage (primary tier).
    ("research", "synthesize"): ModelTier.PRIMARY,
    # Eval harness — LLM-as-judge scoring of drafts against the golden set. Primary
    # tier: judge quality directly gates ship/no-ship regression decisions.
    ("evals", "judge"): ModelTier.PRIMARY,
    # Learning loop — periodic reflection over recent feedback to propose changes.
    # Primary tier: infrequent (weekly-ish), so cost impact is negligible, but a
    # bad reflection produces bad proposals even though a human still reviews them.
    ("learning", "reflect"): ModelTier.PRIMARY,
}


def _resolve(tier: ModelTier) -> ModelConfig:
    if tier is ModelTier.PRIMARY:
        model = os.environ.get("ANTHROPIC_MODEL_PRIMARY", _DEFAULT_ANTHROPIC_MODEL_PRIMARY)
        return ModelConfig(tier=tier, provider=ModelProvider.ANTHROPIC, model=model)
    if tier is ModelTier.CHEAP:
        model = os.environ.get("ANTHROPIC_MODEL_CHEAP", _DEFAULT_ANTHROPIC_MODEL_CHEAP)
        return ModelConfig(tier=tier, provider=ModelProvider.ANTHROPIC, model=model)
    if tier is ModelTier.WORKER:
        model = os.environ.get("HERMES_MODEL", _DEFAULT_HERMES_MODEL)
        endpoint = os.environ.get("HERMES_ENDPOINT", _DEFAULT_HERMES_ENDPOINT)
        return ModelConfig(tier=tier, provider=ModelProvider.HERMES, model=model, endpoint=endpoint)
    raise ValueError(f"Unhandled model tier: {tier!r}")  # pragma: no cover — exhaustive over ModelTier


def route(agent: str, step: str) -> ModelConfig:
    """Resolve the model tier/identifier for one runtime-agent sub-step.

    Raises ValueError for an unregistered (agent, step) pair rather than
    silently defaulting to a tier — a silent default could route a
    sensitive step (e.g. a reply draft) to the wrong tier without anyone
    noticing, which is worse than a loud failure at call time.
    """
    try:
        tier = _ROUTING_TABLE[(agent, step)]
    except KeyError as exc:
        raise ValueError(
            f"No routing entry for agent={agent!r} step={step!r}. "
            "Register it in model_router._ROUTING_TABLE before calling route()."
        ) from exc
    return _resolve(tier)


def registered_steps() -> list[tuple[str, str]]:
    """All (agent, step) pairs currently routable. Used by tests/audits."""
    return list(_ROUTING_TABLE.keys())
