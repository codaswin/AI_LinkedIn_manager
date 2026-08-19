"""Model routing table for the AI LinkedIn Manager's 5 runtime agents.

This is the ONLY file in the codebase permitted to reference a specific
Anthropic/Hermes model name as a literal string — see CLAUDE.md's Forbidden
list: "Hardcoded API keys/model names in code — env vars + llmops/model_router.py".
Every other module must obtain a model identifier by calling route(); it must
never construct or hardcode one itself.

route() is called BY the harness's run_step() — it never calls an LLM API on
its own initiative, and no tool or agent may call an LLM directly, bypassing
the harness (CLAUDE.md non-negotiable #1).

route_and_call() (bottom of this file) is the first real, live-model
implementation of the `llm_client` callable every agent already accepts —
everything through Phase 3 only ever ran against injectable fakes
(harness/loop.py's own docstring: "does not talk to a real model yet").
Passing `route_and_call` as `llm_client` anywhere `run_step()` is invoked is
what turns this from a fully-tested scaffold into a live system.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

import structlog
from app.context.assembler import assemble_context
from app.context.budget import get_budget, register_tier_budget
from app.harness.loop import ToolCallRequest
from app.llmops import cost_tracker, tracer
from app.llmops.anthropic_client import call_anthropic
from app.llmops.hermes_client import call_hermes
from app.llmops.openai_client import call_openai
from app.llmops.prompt_registry import get_prompt
from app.tenancy.credentials import resolve_credential

if TYPE_CHECKING:
    from app.harness.loop import AgentRunConfig
    from app.harness.state import AgentState

logger = structlog.get_logger(__name__)


class ModelTier(str, Enum):
    """Which inference tier a runtime-agent step is routed to."""

    PRIMARY = "primary"
    CHEAP = "cheap"
    WORKER = "worker"


class ModelProvider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    HERMES = "hermes"


# Fallback model identifiers, used only when the corresponding env var is
# unset. These are literal model-name strings by design (see module
# docstring) — do not copy them into any other file.
_DEFAULT_ANTHROPIC_MODEL_PRIMARY = "claude-sonnet-5"
_DEFAULT_ANTHROPIC_MODEL_CHEAP = "claude-haiku-4-5"
# gpt-4o-mini for BOTH tiers, not just cheap: whoever configures OpenAI as
# the provider is opting out of Anthropic's primary/cheap quality split in
# favor of minimizing token cost across every step — see PRIMARY's own
# comment on _resolve() below.
_DEFAULT_OPENAI_MODEL_PRIMARY = "gpt-4o-mini"
_DEFAULT_OPENAI_MODEL_CHEAP = "gpt-4o-mini"
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


def _resolve_credential_if_known(env_var: str) -> str | None:
    """resolve_credential(), tolerant of no tenancy context being set yet.

    A handful of call sites (agent modules building their default
    AgentRunConfig at *import time*, before any request/job has ever run)
    call route() with no current user established — get_current_user_id()
    raising there isn't a real "which user's key" ambiguity, just "we don't
    know yet," which is equivalent to "no key" for provider auto-detection
    purposes. Every genuine per-user path (composio_client, openai_client,
    anthropic_client) calls get_current_user_id() directly and still raises
    loudly, unaffected by this.
    """
    try:
        return resolve_credential(env_var)
    except RuntimeError:
        return None


def _hosted_provider() -> ModelProvider:
    """Which hosted provider backs the PRIMARY/CHEAP tiers (WORKER is always Hermes).

    LLM_PROVIDER wins when set explicitly — this is deployment-wide tuning
    config (see plans/peaceful-scribbling-tiger.md), not a per-user secret,
    so it stays a plain env var. Otherwise, auto-detect from whichever API
    key the *current user* has saved — ANTHROPIC_API_KEY first (keeps prior
    behavior unchanged for anyone already relying on the Anthropic default),
    else OPENAI_API_KEY, so an OpenAI-only setup works without also having to
    know to set LLM_PROVIDER. Falls back to ANTHROPIC when neither key is
    set, which reproduces the original "ANTHROPIC_API_KEY is not set" error
    rather than inventing a new failure mode.

    Reads via resolve_credential(), not os.environ, since OPENAI_API_KEY/
    ANTHROPIC_API_KEY are per-user credentials (Stage 1) — os.environ is
    never populated for these anymore.
    """
    explicit = os.environ.get("LLM_PROVIDER")
    if explicit:
        try:
            return ModelProvider(explicit.strip().lower())
        except ValueError as exc:
            valid = [p.value for p in ModelProvider]
            raise ValueError(f"Unknown LLM_PROVIDER={explicit!r}. Must be one of: {valid}") from exc
    if _resolve_credential_if_known("ANTHROPIC_API_KEY"):
        return ModelProvider.ANTHROPIC
    if _resolve_credential_if_known("OPENAI_API_KEY"):
        return ModelProvider.OPENAI
    return ModelProvider.ANTHROPIC


def _resolve(tier: ModelTier) -> ModelConfig:
    if tier is ModelTier.WORKER:
        model = os.environ.get("HERMES_MODEL", _DEFAULT_HERMES_MODEL)
        endpoint = os.environ.get("HERMES_ENDPOINT", _DEFAULT_HERMES_ENDPOINT)
        return ModelConfig(tier=tier, provider=ModelProvider.HERMES, model=model, endpoint=endpoint)

    provider = _hosted_provider()
    if provider is ModelProvider.OPENAI:
        env_var = "OPENAI_MODEL_PRIMARY" if tier is ModelTier.PRIMARY else "OPENAI_MODEL_CHEAP"
        default = _DEFAULT_OPENAI_MODEL_PRIMARY if tier is ModelTier.PRIMARY else _DEFAULT_OPENAI_MODEL_CHEAP
        model = os.environ.get(env_var, default)
        return ModelConfig(tier=tier, provider=ModelProvider.OPENAI, model=model)
    if provider is ModelProvider.ANTHROPIC:
        env_var = "ANTHROPIC_MODEL_PRIMARY" if tier is ModelTier.PRIMARY else "ANTHROPIC_MODEL_CHEAP"
        default = _DEFAULT_ANTHROPIC_MODEL_PRIMARY if tier is ModelTier.PRIMARY else _DEFAULT_ANTHROPIC_MODEL_CHEAP
        model = os.environ.get(env_var, default)
        return ModelConfig(tier=tier, provider=ModelProvider.ANTHROPIC, model=model)
    raise ValueError(f"Unhandled hosted provider for tier {tier!r}: {provider!r}")  # pragma: no cover


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


# ---------------------------------------------------------------------------
# Token budgets — context.budget owns the *mechanism* (register_tier_budget /
# get_budget), model_router owns the *numbers*, since a tier's usable context
# window is model config (CLAUDE.md: no hardcoded model config outside this
# file). Env-overridable so a different model's real window doesn't need a
# code change.
# ---------------------------------------------------------------------------

_DEFAULT_TIER_BUDGET_TOKENS: dict[str, int] = {
    ModelTier.PRIMARY.value: 150_000,
    ModelTier.CHEAP.value: 150_000,
    ModelTier.WORKER.value: 24_000,
}


def _register_default_tier_budgets() -> None:
    for tier_value, default_tokens in _DEFAULT_TIER_BUDGET_TOKENS.items():
        env_var = f"CONTEXT_BUDGET_{tier_value.upper()}_TOKENS"
        tokens = int(os.environ.get(env_var, default_tokens))
        register_tier_budget(tier_value, tokens)


# Deliberately NOT called once at import time: context.budget.TIER_BUDGETS is
# a shared global dict, and other modules' own tests (context/budget.py's)
# legitimately clear it to test registration/lookup in isolation. Calling
# this at the top of every route_and_call() instead — cheap (a few dict
# writes) and makes correctness independent of what any other test or module
# has done to that global since this process started.


# ---------------------------------------------------------------------------
# Pricing — $ per 1,000 tokens. These are PLACEHOLDER DEFAULTS, not verified
# real provider pricing (this project's default model names —
# "claude-sonnet-5", "claude-haiku-4-5" — are themselves placeholders; see
# _DEFAULT_ANTHROPIC_MODEL_* above). Override every one of these via env var
# with your actual plan's real per-token price before trusting cost_cap's
# $/day enforcement for anything real. Hermes defaults to $0 since it's
# self-hosted — no per-token API charge, only infra cost this module doesn't
# track.
# ---------------------------------------------------------------------------


def _price_per_1k(env_var: str, default: float) -> float:
    raw = os.environ.get(env_var)
    return float(raw) if raw else default


@dataclass
class RouteAndCallResponse:
    """Satisfies harness.loop.LLMResponse's Protocol shape — the same

    (text, tool_calls, cost_usd, confidence, goal_achieved) attributes every
    FakeLLMResponse in this codebase's test suite already provides.
    """

    text: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    cost_usd: float = 0.0
    confidence: float | None = None
    goal_achieved: bool = False


def _build_user_content(state: AgentState, budget_tokens: int) -> str:
    """Assembles one call's user-turn content via context.assembler, honoring

    the priority order (current_task > rag_context > older_conversation)
    and the tier's token budget. system_prompt/safety_rules are NOT part of
    this — they go through Anthropic's/OpenAI's dedicated `system` field
    instead (mandatory-tier handling per assembler.py is for the case where
    a caller wants system content folded into one blob; here the provider
    APIs already give us a first-class, never-truncated system channel, so
    using it directly is strictly better than routing system_prompt through
    assemble_context just to reassemble it here).
    """
    components: dict[str, object] = {"current_task": state.current_task}
    if state.scratchpad:
        components["rag_context"] = json.dumps(state.scratchpad, default=str, sort_keys=True)
    if state.conversation:
        components["older_conversation"] = "\n".join(
            f"[{turn.get('role', '?')}] {turn.get('content', '')}" for turn in state.conversation
        )
    blocks = assemble_context(components, budget_tokens)
    return "\n\n".join(f"### {block['label']}\n{block['content']}" for block in blocks)


async def route_and_call(*, state: AgentState, config: AgentRunConfig) -> RouteAndCallResponse:
    """The live `llm_client` implementation — pass this wherever run_step()

    needs a real model instead of a test fake. Every step CLAUDE.md rule #1
    requires happens here, in order: budget check before the call, the call
    itself, cost recording, and tracing — all before returning.

    Known limitation: `tool_calls` is always []. No runtime agent built in
    this codebase ever passes a non-None `tool_executor` to run_step() (every
    agent calls tools.registry.execute_tool() directly, outside the harness
    loop) — wiring the harness's own tool-calling path is future work for
    whichever agent first needs it, not invented here speculatively.
    """
    _register_default_tier_budgets()
    cost_tracker.check_budget()

    model_config = route(config.agent_name, config.task_type)
    system_prompt = get_prompt(config.agent_name)
    budget_tokens = get_budget(model_config.tier.value)
    user_content = _build_user_content(state, budget_tokens)

    start = time.perf_counter()
    if model_config.provider is ModelProvider.HERMES:
        if model_config.endpoint is None:
            raise ValueError(f"Hermes tier resolved with no endpoint configured: {model_config!r}")
        hermes_result = await call_hermes(
            endpoint=model_config.endpoint,
            model=model_config.model,
            system_prompt=system_prompt,
            user_content=user_content,
        )
        result_text, result_confidence, result_goal_achieved = (
            hermes_result.text,
            hermes_result.confidence,
            hermes_result.goal_achieved,
        )
        input_tokens, output_tokens = hermes_result.input_tokens, hermes_result.output_tokens
        price_in = _price_per_1k("HERMES_PRICE_IN_PER_1K", 0.0)
        price_out = _price_per_1k("HERMES_PRICE_OUT_PER_1K", 0.0)
    elif model_config.provider is ModelProvider.OPENAI:
        openai_result = await call_openai(
            model=model_config.model, system_prompt=system_prompt, user_content=user_content
        )
        result_text, result_confidence, result_goal_achieved = (
            openai_result.text,
            openai_result.confidence,
            openai_result.goal_achieved,
        )
        input_tokens, output_tokens = openai_result.input_tokens, openai_result.output_tokens
        # gpt-4o-mini list pricing at time of writing: $0.15/1M in, $0.60/1M
        # out — same placeholder-not-verified caveat as the Anthropic prices
        # below (module docstring). Both tiers default to the same price
        # here because both tiers default to the same model (gpt-4o-mini).
        if model_config.tier is ModelTier.PRIMARY:
            price_in = _price_per_1k("OPENAI_PRICE_PRIMARY_IN_PER_1K", 0.00015)
            price_out = _price_per_1k("OPENAI_PRICE_PRIMARY_OUT_PER_1K", 0.0006)
        else:
            price_in = _price_per_1k("OPENAI_PRICE_CHEAP_IN_PER_1K", 0.00015)
            price_out = _price_per_1k("OPENAI_PRICE_CHEAP_OUT_PER_1K", 0.0006)
    else:
        anthropic_result = await call_anthropic(
            model=model_config.model, system_prompt=system_prompt, user_content=user_content
        )
        result_text, result_confidence, result_goal_achieved = (
            anthropic_result.text,
            anthropic_result.confidence,
            anthropic_result.goal_achieved,
        )
        input_tokens, output_tokens = anthropic_result.input_tokens, anthropic_result.output_tokens
        if model_config.tier is ModelTier.PRIMARY:
            price_in = _price_per_1k("ANTHROPIC_PRICE_PRIMARY_IN_PER_1K", 0.003)
            price_out = _price_per_1k("ANTHROPIC_PRICE_PRIMARY_OUT_PER_1K", 0.015)
        else:
            price_in = _price_per_1k("ANTHROPIC_PRICE_CHEAP_IN_PER_1K", 0.0008)
            price_out = _price_per_1k("ANTHROPIC_PRICE_CHEAP_OUT_PER_1K", 0.004)

    latency_ms = (time.perf_counter() - start) * 1000
    cost_usd = (input_tokens / 1000 * price_in) + (output_tokens / 1000 * price_out)
    cost_tracker.record_cost(cost_usd)

    tracer.trace_llm_call(
        agent=config.agent_name,
        step=config.task_type,
        model=model_config.model,
        provider=model_config.provider.value,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        inputs={"system_prompt_chars": len(system_prompt), "user_content_chars": len(user_content)},
        outputs={"text_chars": len(result_text), "goal_achieved": result_goal_achieved},
        tokens_in=input_tokens,
        tokens_out=output_tokens,
    )

    return RouteAndCallResponse(
        text=result_text,
        tool_calls=[],
        cost_usd=cost_usd,
        confidence=result_confidence,
        goal_achieved=result_goal_achieved,
    )
