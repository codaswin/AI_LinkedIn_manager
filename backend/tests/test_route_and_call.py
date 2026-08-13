from __future__ import annotations

import pytest
from app.harness.loop import AgentRunConfig
from app.harness.state import AgentState, RuntimeAgentName
from app.llmops import cost_tracker, model_router, tracer
from app.llmops.anthropic_client import AnthropicCallResult
from app.llmops.hermes_client import HermesCallResult
from app.llmops.model_router import RouteAndCallResponse, route_and_call
from app.llmops.openai_client import OpenAICallResult
from app.llmops.prompt_registry import register_prompt


@pytest.fixture(autouse=True)
def _reset_cost_and_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    cost_tracker.reset_for_testing()
    tracer.reset_sink_for_testing()
    register_prompt("content_writer", "You are the Content Writer.")
    register_prompt("engagement", "You are the Engagement Agent.")
    # Every test in this file monkeypatches call_anthropic/call_openai/call_hermes
    # directly rather than hitting a real API — but model_router._hosted_provider()
    # auto-detects the provider from whichever of these keys is actually present
    # in the environment. Left unmocked, a real ANTHROPIC_API_KEY/OPENAI_API_KEY
    # sitting in the ambient shell environment silently steers a test onto the
    # OTHER provider's (unmocked) branch, which either fails confusingly or —
    # worse — falls through to a real, billed API call. Clearing both plus
    # LLM_PROVIDER makes provider selection deterministic regardless of what's
    # exported in whatever shell actually runs this suite.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    yield
    cost_tracker.reset_for_testing()


def _state(**overrides) -> AgentState:
    defaults = {
        "task_id": "t1",
        "agent_name": RuntimeAgentName.CONTENT_WRITER,
        "current_task": "Write a post about agentic AI",
        "scratchpad": {"brief": {"topic": "agentic AI"}},
    }
    defaults.update(overrides)
    return AgentState(**defaults)


def _config(**overrides) -> AgentRunConfig:
    defaults = {"agent_name": "content_writer", "allowed_tools": [], "model_tier": "primary", "task_type": "draft"}
    defaults.update(overrides)
    return AgentRunConfig(**defaults)


async def test_route_and_call_uses_anthropic_for_primary_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def fake_call_anthropic(*, model, system_prompt, user_content):
        captured["model"] = model
        captured["system_prompt"] = system_prompt
        captured["user_content"] = user_content
        return AnthropicCallResult(text="Here's your post.", confidence=0.9, goal_achieved=True, input_tokens=1000, output_tokens=500)

    monkeypatch.setattr(model_router, "call_anthropic", fake_call_anthropic)

    response = await route_and_call(state=_state(), config=_config())

    assert isinstance(response, RouteAndCallResponse)
    assert response.text == "Here's your post."
    assert response.confidence == 0.9
    assert response.goal_achieved is True
    assert response.tool_calls == []
    assert captured["model"] == "claude-sonnet-5"
    assert captured["system_prompt"] == "You are the Content Writer."
    assert "Write a post about agentic AI" in captured["user_content"]


async def test_route_and_call_uses_openai_when_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    captured = {}

    async def fake_call_openai(*, model, system_prompt, user_content):
        captured["model"] = model
        captured["system_prompt"] = system_prompt
        captured["user_content"] = user_content
        return OpenAICallResult(text="Here's your post.", confidence=0.9, goal_achieved=True, input_tokens=1000, output_tokens=500)

    monkeypatch.setattr(model_router, "call_openai", fake_call_openai)

    response = await route_and_call(state=_state(), config=_config())

    assert response.text == "Here's your post."
    assert captured["model"] == "gpt-4o-mini"
    assert captured["system_prompt"] == "You are the Content Writer."
    assert "Write a post about agentic AI" in captured["user_content"]
    # gpt-4o-mini list pricing default: $0.00015/1K in, $0.0006/1K out.
    assert response.cost_usd == pytest.approx(1000 / 1000 * 0.00015 + 500 / 1000 * 0.0006)


async def test_route_and_call_uses_hermes_for_worker_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    register_prompt("engagement", "You are the Engagement Agent.")
    captured = {}

    async def fake_call_hermes(*, endpoint, model, system_prompt, user_content):
        captured["endpoint"] = endpoint
        captured["model"] = model
        return HermesCallResult(text="triaged", confidence=None, goal_achieved=True, input_tokens=200, output_tokens=50)

    monkeypatch.setattr(model_router, "call_hermes", fake_call_hermes)

    state = _state(agent_name=RuntimeAgentName.ENGAGEMENT, current_task="Triage a notification")
    config = _config(agent_name="engagement", model_tier="worker", task_type="triage")

    response = await route_and_call(state=state, config=config)

    assert response.text == "triaged"
    assert response.confidence is None
    assert captured["endpoint"] == "http://localhost:8001/v1"
    assert captured["model"] == "hermes-3"


async def test_route_and_call_computes_cost_from_token_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_call_anthropic(*, model, system_prompt, user_content):
        return AnthropicCallResult(text="x", confidence=0.5, goal_achieved=True, input_tokens=1000, output_tokens=1000)

    monkeypatch.setattr(model_router, "call_anthropic", fake_call_anthropic)
    monkeypatch.setenv("ANTHROPIC_PRICE_PRIMARY_IN_PER_1K", "0.01")
    monkeypatch.setenv("ANTHROPIC_PRICE_PRIMARY_OUT_PER_1K", "0.02")

    response = await route_and_call(state=_state(), config=_config())

    assert response.cost_usd == pytest.approx(0.01 + 0.02)


async def test_route_and_call_hermes_defaults_to_zero_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_call_hermes(*, endpoint, model, system_prompt, user_content):
        return HermesCallResult(text="x", confidence=None, goal_achieved=True, input_tokens=5000, output_tokens=5000)

    monkeypatch.setattr(model_router, "call_hermes", fake_call_hermes)

    state = _state(agent_name=RuntimeAgentName.ENGAGEMENT, current_task="Triage")
    config = _config(agent_name="engagement", model_tier="worker", task_type="triage")
    response = await route_and_call(state=state, config=config)

    assert response.cost_usd == 0.0


async def test_route_and_call_records_cost_against_the_daily_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_call_anthropic(*, model, system_prompt, user_content):
        return AnthropicCallResult(text="x", confidence=0.5, goal_achieved=True, input_tokens=1000, output_tokens=1000)

    monkeypatch.setattr(model_router, "call_anthropic", fake_call_anthropic)
    monkeypatch.setenv("ANTHROPIC_PRICE_PRIMARY_IN_PER_1K", "1.0")
    monkeypatch.setenv("ANTHROPIC_PRICE_PRIMARY_OUT_PER_1K", "1.0")

    before = cost_tracker.get_today_spend()
    response = await route_and_call(state=_state(), config=_config())
    after = cost_tracker.get_today_spend()

    assert after - before == pytest.approx(response.cost_usd)


async def test_route_and_call_refuses_once_budget_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_COST_BUDGET_DAILY_USD", "0.0001")
    cost_tracker.record_cost(1.0)

    async def failing_call_anthropic(*, model, system_prompt, user_content):
        raise AssertionError("must never reach the model call once the budget is exceeded")

    monkeypatch.setattr(model_router, "call_anthropic", failing_call_anthropic)

    with pytest.raises(cost_tracker.CostBudgetExceededError):
        await route_and_call(state=_state(), config=_config())


async def test_route_and_call_traces_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_call_anthropic(*, model, system_prompt, user_content):
        return AnthropicCallResult(text="x", confidence=0.5, goal_achieved=True, input_tokens=10, output_tokens=10)

    monkeypatch.setattr(model_router, "call_anthropic", fake_call_anthropic)

    captured_trace = {}
    monkeypatch.setattr(
        model_router.tracer,
        "trace_llm_call",
        lambda **kwargs: captured_trace.update(kwargs) or "trace-id",
    )

    await route_and_call(state=_state(), config=_config())

    assert captured_trace["agent"] == "content_writer"
    assert captured_trace["step"] == "draft"
    assert captured_trace["provider"] == "anthropic"
    assert captured_trace["tokens_in"] == 10
    assert captured_trace["tokens_out"] == 10


async def test_route_and_call_response_satisfies_llm_response_protocol_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Structural check: run_step() only ever touches these 5 attributes on

    whatever it's given as llm_client's return value — this is what makes
    RouteAndCallResponse a drop-in replacement for every FakeLLMResponse
    used throughout the rest of the test suite.
    """
    async def fake_call_anthropic(*, model, system_prompt, user_content):
        return AnthropicCallResult(text="x", confidence=0.5, goal_achieved=True, input_tokens=10, output_tokens=10)

    monkeypatch.setattr(model_router, "call_anthropic", fake_call_anthropic)

    response = await route_and_call(state=_state(), config=_config())
    for attr in ("text", "tool_calls", "cost_usd", "confidence", "goal_achieved"):
        assert hasattr(response, attr)


async def test_route_and_call_end_to_end_through_run_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real integration check: run_step() itself, not just route_and_call

    in isolation, wired end to end with a mocked provider call.
    """
    from app.harness.loop import run_step

    async def fake_call_anthropic(*, model, system_prompt, user_content):
        return AnthropicCallResult(text="Final post copy.", confidence=0.9, goal_achieved=True, input_tokens=100, output_tokens=100)

    monkeypatch.setattr(model_router, "call_anthropic", fake_call_anthropic)

    state = _state()
    result_state = await run_step(state, _config(), route_and_call, tool_executor=None)

    assert result_state.conversation[-1]["content"] == "Final post copy."
    assert result_state.confidence == 0.9
    assert result_state.cost_so_far_usd > 0
