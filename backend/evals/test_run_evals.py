from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from app.agents.content_writer import write_post
from app.agents.engagement import handle_notification
from app.harness.loop import ToolCallRequest
from app.llmops.prompt_registry import register_prompt
from app.tenancy import context as tenancy_context
from evals.llm_judge import JUDGE_SYSTEM_PROMPT
from evals.run_evals import (
    RegressionError,
    compare_to_baseline,
    load_jsonl,
    run_all,
    run_post_evals,
    run_reply_evals,
)

register_prompt("evals", JUDGE_SYSTEM_PROMPT)


@pytest.fixture(autouse=True)
def _tenancy_context():
    token = tenancy_context.set_current_user_id("user-evals-test")
    yield
    tenancy_context.reset_current_user_id(token)


@dataclass
class FakeLLMResponse:
    text: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    cost_usd: float = 0.0
    confidence: float | None = None
    goal_achieved: bool = True


JUDGE_TEXT = json.dumps(
    {"brand_voice_fidelity": 4, "groundedness": 4, "reply_appropriateness": 4, "reasoning": "consistent"}
)


async def _fake_llm_client(*, state: Any, config: Any) -> FakeLLMResponse:
    if config.task_type == "judge":
        return FakeLLMResponse(text=JUDGE_TEXT)
    if config.agent_name == "content_writer":
        topic = state.scratchpad["brief"]["topic"]
        return FakeLLMResponse(text=f"A grounded post about {topic}, written in brand voice.", confidence=0.9)
    if config.agent_name == "engagement":
        return FakeLLMResponse(text="Thanks for the thoughtful question — appreciate it!", confidence=0.9)
    return FakeLLMResponse(text="{}", confidence=0.9)


def test_load_jsonl_parses_every_line(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text('{"id": "1"}\n{"id": "2"}\n\n')
    cases = load_jsonl(path)
    assert cases == [{"id": "1"}, {"id": "2"}]


# ---------------------------------------------------------------------------
# End-to-end against the REAL runtime agents (fake llm_client only) — this is
# the integration check that the eval harness actually plugs into
# content_writer.write_post / engagement.handle_notification without needing
# any changes to either.
# ---------------------------------------------------------------------------


async def test_run_post_evals_against_real_content_writer() -> None:
    result = await run_post_evals(_fake_llm_client, write_post)

    assert result["case_count"] >= 15
    assert result["must_avoid_pass_rate"] == 1.0  # fake drafts never contain a must_avoid phrase
    assert result["avg_brand_voice_fidelity"] == 4.0
    assert result["avg_groundedness"] == 4.0
    assert len(result["per_case"]) == result["case_count"]


async def test_run_reply_evals_against_real_engagement_agent() -> None:
    result = await run_reply_evals(_fake_llm_client, handle_notification)

    assert result["case_count"] >= 12
    escalation = result["escalation"]
    # All keyword-verifiable escalate cases are caught by the real refusal-topic
    # detector regardless of the fake client's (always-confident) judgment —
    # only the one deliberately-LLM-judgment-only case can be missed.
    assert escalation["missed_escalation_count"] <= 1
    assert escalation["wrongly_escalated_count"] == 0


async def test_run_all_returns_both_sections() -> None:
    result = await run_all(_fake_llm_client, write_post, handle_notification)
    assert set(result.keys()) == {"posts", "replies"}


# ---------------------------------------------------------------------------
# compare_to_baseline
# ---------------------------------------------------------------------------


def test_compare_to_baseline_establishes_baseline_on_first_run(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    current = {"posts": {"avg_brand_voice_fidelity": 4.0}}

    compare_to_baseline(current, baseline_path=baseline_path)

    assert baseline_path.exists()
    assert json.loads(baseline_path.read_text()) == current


def test_compare_to_baseline_passes_when_no_regression(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"posts": {"avg_brand_voice_fidelity": 4.0}}))

    current = {"posts": {"avg_brand_voice_fidelity": 4.0}}
    compare_to_baseline(current, baseline_path=baseline_path)  # must not raise


def test_compare_to_baseline_passes_when_score_improves(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"posts": {"avg_brand_voice_fidelity": 3.5}}))

    current = {"posts": {"avg_brand_voice_fidelity": 4.5}}
    compare_to_baseline(current, baseline_path=baseline_path)  # must not raise


def test_compare_to_baseline_raises_past_threshold(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"posts": {"avg_brand_voice_fidelity": 4.0}}))

    current = {"posts": {"avg_brand_voice_fidelity": 3.5}}  # 12.5% drop
    with pytest.raises(RegressionError, match="avg_brand_voice_fidelity dropped"):
        compare_to_baseline(current, baseline_path=baseline_path, threshold_pct=5.0)


def test_compare_to_baseline_tolerates_drop_under_threshold(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"posts": {"avg_brand_voice_fidelity": 4.0}}))

    current = {"posts": {"avg_brand_voice_fidelity": 3.9}}  # 2.5% drop, under 5% threshold
    compare_to_baseline(current, baseline_path=baseline_path, threshold_pct=5.0)  # must not raise


def test_compare_to_baseline_ignores_metrics_missing_from_either_side(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"posts": {}}))

    current = {"posts": {"avg_brand_voice_fidelity": 4.0}}
    compare_to_baseline(current, baseline_path=baseline_path)  # must not raise — nothing to compare
