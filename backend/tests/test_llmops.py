"""Tests for the llmops foundation: model_router, cost_tracker, tracer, prompt_registry.

Also includes a static check that no literal Anthropic/Hermes model-identifier
string appears anywhere under backend/app/ outside of model_router.py, per
the project rules ("Hardcoded API keys/model names in code — env
vars + llmops/model_router.py").
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from app.llmops import cost_tracker, prompt_registry, tracer
from app.llmops.model_router import ModelProvider, ModelTier, route


@pytest.fixture(autouse=True)
def _reset_llmops_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate each test from module-level mutable state and env vars.

    prompt_registry is snapshotted and restored rather than left cleared:
    every runtime agent module registers its system prompt exactly once, at
    import time, and that import is cached process-wide. Clearing the
    registry to empty after the last test in this file would permanently
    starve every later test file (in any collection order) that relies on
    those one-time registrations having survived.
    """
    cost_tracker.reset_for_testing()
    prompt_snapshot = prompt_registry.snapshot_for_testing()
    prompt_registry.reset_for_testing()
    tracer.reset_sink_for_testing()
    monkeypatch.delenv("LLM_COST_BUDGET_DAILY_USD", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL_PRIMARY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL_CHEAP", raising=False)
    monkeypatch.delenv("OPENAI_MODEL_PRIMARY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL_CHEAP", raising=False)
    monkeypatch.delenv("HERMES_MODEL", raising=False)
    monkeypatch.delenv("HERMES_ENDPOINT", raising=False)
    monkeypatch.delenv("TRACE_SINK", raising=False)
    yield
    cost_tracker.reset_for_testing()
    prompt_registry.restore_for_testing(prompt_snapshot)
    tracer.reset_sink_for_testing()


# ---------------------------------------------------------------------------
# model_router.route()
# ---------------------------------------------------------------------------


class TestRouting:
    def test_content_writer_draft_resolves_to_primary(self) -> None:
        config = route("content_writer", "draft")
        assert config.tier is ModelTier.PRIMARY
        assert config.provider is ModelProvider.ANTHROPIC

    def test_content_strategist_plan_resolves_to_cheap(self) -> None:
        config = route("content_strategist", "plan")
        assert config.tier is ModelTier.CHEAP
        assert config.provider is ModelProvider.ANTHROPIC

    def test_engagement_triage_resolves_to_worker(self) -> None:
        config = route("engagement", "triage")
        assert config.tier is ModelTier.WORKER
        assert config.provider is ModelProvider.HERMES

    def test_engagement_draft_resolves_to_primary(self) -> None:
        config = route("engagement", "draft")
        assert config.tier is ModelTier.PRIMARY
        assert config.provider is ModelProvider.ANTHROPIC

    def test_engagement_uses_two_different_tiers_per_step(self) -> None:
        # The same agent must route differently depending on the step name —
        # routing is per (agent, step), not per agent.
        triage = route("engagement", "triage")
        draft = route("engagement", "draft")
        assert triage.tier != draft.tier

    def test_research_triage_resolves_to_worker(self) -> None:
        config = route("research", "triage")
        assert config.tier is ModelTier.WORKER
        assert config.provider is ModelProvider.HERMES

    def test_research_digest_resolves_to_primary(self) -> None:
        config = route("research", "digest")
        assert config.tier is ModelTier.PRIMARY
        assert config.provider is ModelProvider.ANTHROPIC

    def test_research_uses_two_different_tiers_per_step(self) -> None:
        triage = route("research", "triage")
        digest = route("research", "digest")
        assert triage.tier != digest.tier

    def test_analytics_summarize_resolves_to_cheap(self) -> None:
        config = route("analytics", "summarize")
        assert config.tier is ModelTier.CHEAP

    def test_unregistered_agent_step_raises(self) -> None:
        with pytest.raises(ValueError, match="No routing entry"):
            route("nonexistent_agent", "nonexistent_step")

    def test_worker_tier_carries_hermes_endpoint(self) -> None:
        config = route("engagement", "triage")
        assert config.endpoint is not None

    def test_model_identifiers_come_from_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_MODEL_PRIMARY", "test-primary-override")
        monkeypatch.setenv("ANTHROPIC_MODEL_CHEAP", "test-cheap-override")
        monkeypatch.setenv("HERMES_MODEL", "test-worker-override")
        monkeypatch.setenv("HERMES_ENDPOINT", "http://test-hermes:9999/v1")

        assert route("content_writer", "draft").model == "test-primary-override"
        assert route("content_strategist", "plan").model == "test-cheap-override"
        worker_config = route("engagement", "triage")
        assert worker_config.model == "test-worker-override"


class TestProviderSelection:
    """OpenAI support: PRIMARY/CHEAP now resolve to whichever hosted provider

    is actually configured (LLM_PROVIDER, or auto-detected from which API
    key is present) rather than being hardcoded to Anthropic. WORKER is
    untouched — it's always Hermes regardless of provider selection.
    """

    def test_defaults_to_anthropic_when_no_key_present(self) -> None:
        config = route("content_writer", "draft")
        assert config.provider is ModelProvider.ANTHROPIC

    def test_auto_detects_openai_when_only_openai_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        config = route("content_writer", "draft")
        assert config.provider is ModelProvider.OPENAI
        assert config.model == "gpt-4o-mini"

    def test_prefers_anthropic_when_both_keys_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        config = route("content_writer", "draft")
        assert config.provider is ModelProvider.ANTHROPIC

    def test_llm_provider_env_var_wins_over_auto_detection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        config = route("content_writer", "draft")
        assert config.provider is ModelProvider.OPENAI

    def test_unknown_llm_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "not-a-real-provider")
        with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
            route("content_writer", "draft")

    def test_openai_cheap_tier_also_defaults_to_the_low_cost_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert route("content_strategist", "plan").model == "gpt-4o-mini"

    def test_openai_model_identifiers_come_from_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_MODEL_PRIMARY", "test-openai-primary")
        monkeypatch.setenv("OPENAI_MODEL_CHEAP", "test-openai-cheap")
        assert route("content_writer", "draft").model == "test-openai-primary"
        assert route("content_strategist", "plan").model == "test-openai-cheap"

    def test_worker_tier_stays_hermes_regardless_of_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        config = route("engagement", "triage")
        assert config.provider is ModelProvider.HERMES

    def test_model_identifiers_have_sane_fallback_when_env_unset(self) -> None:
        # No env vars set (autouse fixture clears them) — route() must still
        # resolve to a non-empty model string, not blow up or return None.
        for agent, step in [
            ("content_writer", "draft"),
            ("content_strategist", "plan"),
            ("engagement", "triage"),
        ]:
            config = route(agent, step)
            assert isinstance(config.model, str) and config.model


# ---------------------------------------------------------------------------
# cost_tracker — hard stop, not a warning
# ---------------------------------------------------------------------------


class TestCostTracker:
    def test_check_budget_passes_when_under_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_COST_BUDGET_DAILY_USD", "10")
        cost_tracker.record_cost(1.0)
        cost_tracker.check_budget()  # must not raise

    def test_hard_stop_once_daily_cap_exceeded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_COST_BUDGET_DAILY_USD", "10")

        # Simulate spend accumulation across several calls, as the harness
        # would after each real LLM response.
        cost_tracker.record_cost(4.0)
        cost_tracker.record_cost(4.0)
        cost_tracker.check_budget()  # still under $10 — must not raise yet
        cost_tracker.record_cost(2.0)  # now at exactly $10

        assert cost_tracker.get_today_spend() == pytest.approx(10.0)

        # The next call attempt must be refused outright — a raised
        # exception, not merely a logged warning.
        with pytest.raises(cost_tracker.CostBudgetExceededError):
            cost_tracker.check_budget()

    def test_exceeding_cap_beyond_the_limit_still_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_COST_BUDGET_DAILY_USD", "10")
        cost_tracker.record_cost(15.0)
        with pytest.raises(cost_tracker.CostBudgetExceededError) as exc_info:
            cost_tracker.check_budget()
        assert exc_info.value.spent_usd == pytest.approx(15.0)
        assert exc_info.value.budget_usd == pytest.approx(10.0)

    def test_default_budget_is_ten_dollars_when_env_unset(self) -> None:
        # LLM_COST_BUDGET_DAILY_USD is unset by the autouse fixture.
        assert cost_tracker.get_cost_summary()["budget_usd"] == pytest.approx(10.0)

    def test_record_cost_rejects_negative_amounts(self) -> None:
        with pytest.raises(ValueError):
            cost_tracker.record_cost(-1.0)

    def test_get_today_spend_accumulates_across_calls(self) -> None:
        cost_tracker.record_cost(0.5)
        cost_tracker.record_cost(0.25)
        assert cost_tracker.get_today_spend() == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# tracer
# ---------------------------------------------------------------------------


class TestTracer:
    def test_trace_llm_call_returns_a_trace_id(self) -> None:
        trace_id = tracer.trace_llm_call(
            agent="content_writer",
            step="draft",
            model="test-model",
            provider="anthropic",
            latency_ms=123.4,
            cost_usd=0.01,
            inputs={"prompt": "hello"},
            outputs={"text": "hi there"},
            tokens_in=10,
            tokens_out=5,
        )
        assert isinstance(trace_id, str) and trace_id

    def test_trace_llm_call_accepts_explicit_trace_id(self) -> None:
        trace_id = tracer.trace_llm_call(
            agent="research",
            step="triage",
            model="test-model",
            provider="hermes",
            latency_ms=10.0,
            cost_usd=0.0,
            inputs="x",
            outputs="y",
            trace_id="fixed-id-123",
        )
        assert trace_id == "fixed-id-123"

    def test_unknown_trace_sink_falls_back_to_local_without_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRACE_SINK", "some_future_hosted_sink")
        tracer.reset_sink_for_testing()
        # Must not raise — falls back to the local sink per module docstring.
        tracer.trace_llm_call(
            agent="a",
            step="b",
            model="m",
            provider="anthropic",
            latency_ms=1.0,
            cost_usd=0.0,
            inputs=None,
            outputs=None,
        )


# ---------------------------------------------------------------------------
# prompt_registry
# ---------------------------------------------------------------------------


class TestPromptRegistry:
    def test_register_and_get_prompt_roundtrip(self) -> None:
        prompt_registry.register_prompt("content_strategist", "You are the strategist.")
        assert prompt_registry.get_prompt("content_strategist") == "You are the strategist."

    def test_get_prompt_without_registration_raises(self) -> None:
        with pytest.raises(KeyError):
            prompt_registry.get_prompt("never_registered_agent")

    def test_register_prompt_creates_new_version_not_in_place_edit(self) -> None:
        v1 = prompt_registry.register_prompt("engagement", "v1 text")
        v2 = prompt_registry.register_prompt("engagement", "v2 text")
        assert v1.version == 1
        assert v2.version == 2
        assert prompt_registry.get_prompt("engagement") == "v2 text"
        history = prompt_registry.get_prompt_history("engagement")
        assert [v.text for v in history] == ["v1 text", "v2 text"]


# ---------------------------------------------------------------------------
# Static check: no literal model-identifier string outside model_router.py
# ---------------------------------------------------------------------------

# Matches concrete Anthropic/OpenAI/Hermes model IDs (e.g. "claude-sonnet-5",
# "claude-haiku-4-5", "gpt-4o-mini", "hermes-3") — NOT bare product-family
# words like "Claude", "GPT", or "Hermes" used in prose/comments elsewhere in
# the codebase, which are legitimate architectural references rather than
# hardcoded identifiers passed to an API call.
_MODEL_ID_PATTERN = re.compile(
    r"claude-(?:sonnet|haiku|opus|fable|mythos)-[a-z0-9][a-z0-9.\-]*"
    r"|gpt-[a-z0-9][a-z0-9.\-]*"
    r"|hermes-[a-z0-9][a-z0-9.\-]*",
    re.IGNORECASE,
)


def test_no_hardcoded_model_id_outside_model_router() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    router_path = app_dir / "llmops" / "model_router.py"
    assert router_path.exists(), "model_router.py should exist under backend/app/llmops/"

    offenders: list[str] = []
    for py_file in app_dir.rglob("*.py"):
        if py_file.resolve() == router_path.resolve():
            continue
        text = py_file.read_text(encoding="utf-8")
        for match in _MODEL_ID_PATTERN.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{py_file.relative_to(app_dir.parent)}:{line_no}: {match.group(0)!r}")

    assert not offenders, "Hardcoded model identifier(s) found outside model_router.py:\n" + "\n".join(
        offenders
    )


def test_model_router_is_the_documented_exception() -> None:
    # Sanity check the router file itself DOES contain the literal model
    # IDs it's responsible for — otherwise the static check above would be
    # vacuously true if someone moved the fallbacks elsewhere.
    router_path = Path(__file__).resolve().parents[1] / "app" / "llmops" / "model_router.py"
    text = router_path.read_text(encoding="utf-8")
    assert _MODEL_ID_PATTERN.search(text), "model_router.py should define the concrete model IDs"
