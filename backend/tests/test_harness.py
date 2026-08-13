from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from app.harness.loop import (
    AgentRunConfig,
    ToolCallRequest,
    ToolResult,
    run_agent,
    run_step,
)
from app.harness.retry import SafetyCriticalError, with_retry
from app.harness.state import (
    AgentState,
    RuntimeAgentName,
    StopReason,
)
from app.harness.stopping_conditions import should_stop

HARNESS_DIR = Path(__file__).resolve().parents[1] / "app" / "harness"


# ---------------------------------------------------------------------------
# run_step() is the only path to the (stubbed) LLM client
# ---------------------------------------------------------------------------


def _calls_named(tree: ast.AST, names: set[str]) -> list[int]:
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name in names:
                hits.append(node.lineno)
    return hits


def test_no_module_besides_loop_invokes_the_llm_client() -> None:
    """Every harness/*.py file other than loop.py must be free of any call

    that looks like invoking an LLM client (by the conventional parameter
    name `llm_client`, or the future model_router entrypoint). This is a
    static proxy for project invariant #1: run_step() is the sole choke point.
    """
    offenders: dict[str, list[int]] = {}
    for path in HARNESS_DIR.glob("*.py"):
        if path.name == "loop.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        hits = _calls_named(tree, {"llm_client", "route_and_call"})
        if hits:
            offenders[path.name] = hits

    assert not offenders, f"LLM client invoked outside loop.py: {offenders}"


def test_llm_client_is_only_called_inside_run_step() -> None:
    """Within loop.py itself, only the run_step function body may call

    llm_client — run_agent and everything else must go through run_step.
    """
    tree = ast.parse((HARNESS_DIR / "loop.py").read_text())
    calling_functions: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            hits = _calls_named(node, {"llm_client"})
            if hits:
                calling_functions.add(node.name)

    assert calling_functions == {"run_step"}


# ---------------------------------------------------------------------------
# Fakes shared by the remaining tests
# ---------------------------------------------------------------------------


@dataclass
class FakeLLMResponse:
    text: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    cost_usd: float = 0.0
    confidence: float | None = None
    goal_achieved: bool = False


def make_state(**overrides: Any) -> AgentState:
    defaults: dict[str, Any] = {
        "task_id": "task-1",
        "agent_name": RuntimeAgentName.CONTENT_WRITER,
        "current_task": "draft a post",
    }
    defaults.update(overrides)
    return AgentState(**defaults)


def make_config(**overrides: Any) -> AgentRunConfig:
    defaults: dict[str, Any] = {
        "agent_name": "content_writer",
        "allowed_tools": ["draft_post", "search_knowledge_base"],
        "model_tier": "primary",
    }
    defaults.update(overrides)
    return AgentRunConfig(**defaults)


# ---------------------------------------------------------------------------
# Tool call logging happens before the loop advances to the next step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_is_logged_with_full_record_after_run_step() -> None:
    state = make_state()
    config = make_config()

    async def fake_llm_client(*, state: AgentState, config: AgentRunConfig) -> FakeLLMResponse:
        return FakeLLMResponse(
            text="drafting",
            tool_calls=[ToolCallRequest(tool_name="draft_post", arguments={"topic": "AI"})],
            cost_usd=0.01,
            goal_achieved=False,
        )

    async def fake_tool_executor(tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        assert tool_name == "draft_post"
        return ToolResult(outputs={"draft_id": "d-1"}, cost_usd=0.0)

    result = await run_step(state, config, fake_llm_client, fake_tool_executor)

    assert len(result.tool_calls) == 1
    record = result.tool_calls[0]
    assert record.tool_name == "draft_post"
    assert record.inputs == {"topic": "AI"}
    assert record.outputs == {"draft_id": "d-1"}
    assert record.latency_ms >= 0
    assert record.cost_usd == 0.0
    assert record.timestamp is not None
    assert result.iteration == 1


@pytest.mark.asyncio
async def test_tool_call_is_visible_before_next_iterations_llm_call() -> None:
    """Prove ordering, not just end state: by the time iteration 2's

    llm_client is invoked, iteration 1's tool call must already be in
    state.tool_calls — i.e. logged before control returned to the loop.
    """
    state = make_state(max_iterations=5)
    config = make_config()
    observed_lengths: list[int] = []

    async def fake_llm_client(*, state: AgentState, config: AgentRunConfig) -> FakeLLMResponse:
        observed_lengths.append(len(state.tool_calls))
        if state.iteration == 0:
            return FakeLLMResponse(
                text="calling tool",
                tool_calls=[ToolCallRequest(tool_name="draft_post", arguments={})],
                cost_usd=0.0,
            )
        return FakeLLMResponse(text="done", goal_achieved=True)

    async def fake_tool_executor(tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(outputs="ok")

    final_state = await run_agent(state, config, fake_llm_client, fake_tool_executor)

    assert observed_lengths == [0, 1]
    assert final_state.stop_reason == StopReason.GOAL_ACHIEVED
    assert len(final_state.tool_calls) == 1


# ---------------------------------------------------------------------------
# Stopping conditions
# ---------------------------------------------------------------------------


def test_should_stop_none_when_within_limits() -> None:
    state = make_state(iteration=1, max_iterations=10, cost_so_far_usd=1.0, budget_usd=10.0)
    assert should_stop(state) is None


def test_should_stop_max_iterations() -> None:
    state = make_state(iteration=10, max_iterations=10, cost_so_far_usd=0.0, budget_usd=10.0)
    assert should_stop(state) == StopReason.MAX_ITERATIONS


def test_should_stop_budget_exceeded() -> None:
    state = make_state(iteration=0, max_iterations=10, cost_so_far_usd=10.0, budget_usd=10.0)
    assert should_stop(state) == StopReason.BUDGET_EXCEEDED


@pytest.mark.asyncio
async def test_run_agent_halts_at_max_iterations() -> None:
    state = make_state(max_iterations=2)
    config = make_config()

    async def fake_llm_client(*, state: AgentState, config: AgentRunConfig) -> FakeLLMResponse:
        return FakeLLMResponse(text="thinking", goal_achieved=False)

    final_state = await run_agent(state, config, fake_llm_client, tool_executor=None)

    assert final_state.stop_reason == StopReason.MAX_ITERATIONS
    assert final_state.iteration == 2


@pytest.mark.asyncio
async def test_run_agent_halts_at_budget_exceeded() -> None:
    state = make_state(max_iterations=10, budget_usd=0.05)
    config = make_config()

    async def fake_llm_client(*, state: AgentState, config: AgentRunConfig) -> FakeLLMResponse:
        return FakeLLMResponse(text="thinking", cost_usd=0.03, goal_achieved=False)

    final_state = await run_agent(state, config, fake_llm_client, tool_executor=None)

    assert final_state.stop_reason == StopReason.BUDGET_EXCEEDED
    assert final_state.cost_so_far_usd >= 0.05


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safety_critical_failure_never_retries() -> None:
    calls = 0

    async def flaky() -> None:
        nonlocal calls
        calls += 1
        raise SafetyCriticalError("ungated publish_post execution detected")

    with pytest.raises(SafetyCriticalError):
        await with_retry(flaky, max_retries=2, base_delay=0.0)

    assert calls == 1


@pytest.mark.asyncio
async def test_non_safety_critical_failure_retries_up_to_two_times_then_succeeds() -> None:
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("transient tool timeout")
        return "ok"

    result = await with_retry(flaky, max_retries=2, base_delay=0.0)

    assert result == "ok"
    assert calls == 3


@pytest.mark.asyncio
async def test_non_safety_critical_failure_raises_after_exhausting_retries() -> None:
    calls = 0

    async def always_fails() -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError("still down")

    with pytest.raises(TimeoutError):
        await with_retry(always_fails, max_retries=2, base_delay=0.0)

    assert calls == 3
