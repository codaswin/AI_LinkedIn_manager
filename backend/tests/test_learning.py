from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from app.harness.loop import ToolCallRequest
from app.harness.state import AgentState
from app.learning.feedback import (
    SIGNAL_APPROVED,
    SIGNAL_EDITED,
    SIGNAL_ENGAGEMENT_OUTCOME,
    SIGNAL_REJECTED,
    capture_feedback,
    recent_engagement_outcomes,
    recent_negative_feedback,
)
from app.learning.proposal_review import (
    ALWAYS_REVIEW_TYPES,
    AUTO_APPLY_CONFIDENCE_THRESHOLD,
    AUTO_APPLY_TYPES,
    ProposalAlreadyDecidedError,
    ProposalNotFoundError,
    approve_proposal,
    list_pending,
    reject_proposal,
    submit_proposal,
)
from app.learning.reflection_job import (
    MIN_FEEDBACK_FOR_REFLECTION,
    REFLECTION_SYSTEM_PROMPT,
    run_reflection,
)
from app.llmops.prompt_registry import get_prompt, register_prompt


@pytest.fixture(autouse=True)
def _ensure_prompt_registered() -> None:
    register_prompt("learning", REFLECTION_SYSTEM_PROMPT)


@dataclass
class FakeLLMResponse:
    text: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    cost_usd: float = 0.0
    confidence: float | None = None
    goal_achieved: bool = True


def _make_llm_client(text: str):
    async def _client(*, state: AgentState, config: Any) -> FakeLLMResponse:
        return FakeLLMResponse(text=text)

    return _client


# ---------------------------------------------------------------------------
# feedback.py
# ---------------------------------------------------------------------------


async def test_capture_feedback_persists_all_fields(db_session) -> None:
    record = await capture_feedback(
        db_session,
        task_id="task-1",
        agent_name="content_writer",
        signal_type=SIGNAL_REJECTED,
        detail="too salesy",
        engagement_stats=None,
        confidence=0.6,
    )
    assert record.task_id == "task-1"
    assert record.agent_name == "content_writer"
    assert record.signal_type == SIGNAL_REJECTED
    assert record.detail == "too salesy"
    assert record.confidence == 0.6
    assert record.id


async def test_recent_negative_feedback_includes_rejected_and_edited_only(db_session) -> None:
    await capture_feedback(db_session, task_id="t1", agent_name="content_writer", signal_type=SIGNAL_APPROVED, detail="fine")
    await capture_feedback(db_session, task_id="t2", agent_name="content_writer", signal_type=SIGNAL_REJECTED, detail="bad tone")
    await capture_feedback(db_session, task_id="t3", agent_name="content_writer", signal_type=SIGNAL_EDITED, detail="fixed a claim")
    await capture_feedback(
        db_session,
        task_id="t4",
        agent_name="content_writer",
        signal_type=SIGNAL_ENGAGEMENT_OUTCOME,
        detail="",
        engagement_stats={"likes": 10},
    )

    negative = await recent_negative_feedback(db_session, days=7)

    assert {r.task_id for r in negative} == {"t2", "t3"}


async def test_recent_negative_feedback_excludes_entries_older_than_window(db_session) -> None:
    from datetime import datetime, timedelta, timezone

    from app.models.feedback import FeedbackRecord

    old_time = datetime.now(timezone.utc) - timedelta(days=30)
    stale = FeedbackRecord(
        id="stale-1", task_id="old", agent_name="content_writer", signal_type=SIGNAL_REJECTED,
        detail="ancient", created_at=old_time,
    )
    db_session.add(stale)
    await db_session.commit()

    await capture_feedback(db_session, task_id="fresh", agent_name="content_writer", signal_type=SIGNAL_REJECTED, detail="recent")

    negative = await recent_negative_feedback(db_session, days=7)
    assert {r.task_id for r in negative} == {"fresh"}


async def test_recent_engagement_outcomes_filters_correctly(db_session) -> None:
    await capture_feedback(db_session, task_id="t1", agent_name="analytics", signal_type=SIGNAL_REJECTED, detail="x")
    await capture_feedback(
        db_session,
        task_id="t2",
        agent_name="analytics",
        signal_type=SIGNAL_ENGAGEMENT_OUTCOME,
        detail="",
        engagement_stats={"likes": 42, "comments": 3},
    )

    outcomes = await recent_engagement_outcomes(db_session, days=7)
    assert len(outcomes) == 1
    assert outcomes[0].task_id == "t2"
    assert outcomes[0].engagement_stats == {"likes": 42, "comments": 3}


# ---------------------------------------------------------------------------
# proposal_review.py — classification + hard non-negotiable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("change_type", sorted(ALWAYS_REVIEW_TYPES))
async def test_always_review_types_never_auto_apply_even_at_max_confidence(db_session, change_type: str) -> None:
    """learning-agent.md's non-negotiable, verbatim: any proposed change that

    touches a system prompt, tool definition, or safety threshold goes into
    human review — never auto-applies, regardless of confidence. This test
    would fail if anyone ever special-cased a "the model seems very sure"
    bypass for these types.
    """
    proposal = await submit_proposal(
        db_session,
        pattern="some pattern",
        change_type=change_type,
        proposed_change="some change",
        confidence=1.0,
    )
    assert proposal.status == "pending"


@pytest.mark.parametrize("change_type", sorted(AUTO_APPLY_TYPES))
async def test_auto_apply_types_auto_apply_at_or_above_threshold(db_session, change_type: str) -> None:
    proposal = await submit_proposal(
        db_session,
        pattern="some pattern",
        change_type=change_type,
        proposed_change="some change",
        confidence=AUTO_APPLY_CONFIDENCE_THRESHOLD,
    )
    assert proposal.status == "auto_applied"
    assert proposal.decided_by == "system:reflection_job"
    assert proposal.decided_at is not None


@pytest.mark.parametrize("change_type", sorted(AUTO_APPLY_TYPES))
async def test_auto_apply_types_below_threshold_default_to_review(db_session, change_type: str) -> None:
    proposal = await submit_proposal(
        db_session,
        pattern="some pattern",
        change_type=change_type,
        proposed_change="some change",
        confidence=AUTO_APPLY_CONFIDENCE_THRESHOLD - 0.01,
    )
    assert proposal.status == "pending"


async def test_unrecognized_change_type_defaults_to_review(db_session) -> None:
    proposal = await submit_proposal(
        db_session,
        pattern="some pattern",
        change_type="some_new_unclassified_type",
        proposed_change="some change",
        confidence=0.99,
    )
    assert proposal.status == "pending"


async def test_approve_proposal_happy_path(db_session) -> None:
    proposal = await submit_proposal(
        db_session, pattern="p", change_type="system_prompt", proposed_change="c", confidence=0.5
    )
    approved = await approve_proposal(db_session, proposal.id, decided_by="human:aswin")
    assert approved.status == "approved"
    assert approved.decided_by == "human:aswin"


async def test_reject_proposal_happy_path(db_session) -> None:
    proposal = await submit_proposal(
        db_session, pattern="p", change_type="system_prompt", proposed_change="c", confidence=0.5
    )
    rejected = await reject_proposal(db_session, proposal.id, decided_by="human:aswin", reason="not convincing")
    assert rejected.status == "rejected"
    assert "not convincing" in rejected.proposed_change


async def test_approve_nonexistent_proposal_raises(db_session) -> None:
    with pytest.raises(ProposalNotFoundError):
        await approve_proposal(db_session, "does-not-exist", decided_by="human:aswin")


async def test_approve_already_decided_proposal_raises(db_session) -> None:
    proposal = await submit_proposal(
        db_session, pattern="p", change_type="system_prompt", proposed_change="c", confidence=0.5
    )
    await approve_proposal(db_session, proposal.id, decided_by="human:aswin")
    with pytest.raises(ProposalAlreadyDecidedError):
        await approve_proposal(db_session, proposal.id, decided_by="human:someone_else")


async def test_list_pending_excludes_decided_and_auto_applied(db_session) -> None:
    pending = await submit_proposal(
        db_session, pattern="p1", change_type="system_prompt", proposed_change="c1", confidence=0.5
    )
    await submit_proposal(
        db_session, pattern="p2", change_type="retrieval_weight", proposed_change="c2", confidence=0.9
    )
    decided = await submit_proposal(
        db_session, pattern="p3", change_type="new_tool", proposed_change="c3", confidence=0.5
    )
    await approve_proposal(db_session, decided.id, decided_by="human:aswin")

    result = await list_pending(db_session)
    assert [p.id for p in result] == [pending.id]


# ---------------------------------------------------------------------------
# reflection_job.py
# ---------------------------------------------------------------------------


async def test_run_reflection_skips_when_insufficient_feedback(db_session) -> None:
    for i in range(MIN_FEEDBACK_FOR_REFLECTION - 1):
        await capture_feedback(db_session, task_id=f"t{i}", agent_name="content_writer", signal_type=SIGNAL_REJECTED, detail="x")

    async def failing_llm_client(*, state: AgentState, config: Any) -> FakeLLMResponse:
        raise AssertionError("LLM must not be called when there's insufficient feedback signal")

    result = await run_reflection(db_session, failing_llm_client)
    assert result["ran"] is False
    assert result["reason"] == "insufficient_feedback"
    assert result["proposals"] == []


async def test_run_reflection_routes_through_run_step_and_submits_proposals(db_session) -> None:
    for i in range(MIN_FEEDBACK_FOR_REFLECTION):
        await capture_feedback(db_session, task_id=f"t{i}", agent_name="content_writer", signal_type=SIGNAL_REJECTED, detail=f"draft {i} too salesy")

    captured: dict[str, Any] = {}

    async def spying_llm_client(*, state: AgentState, config: Any) -> FakeLLMResponse:
        captured["state"] = state
        captured["config"] = config
        return FakeLLMResponse(
            text=json.dumps(
                [
                    {"pattern": "drafts sound salesy", "change_type": "system_prompt", "proposed_change": "add tone guidance", "confidence": 0.9},
                    {"pattern": "top posts cite numbers", "change_type": "few_shot_example", "proposed_change": "add example", "confidence": 0.85},
                ]
            )
        )

    result = await run_reflection(db_session, spying_llm_client)

    assert result["ran"] is True
    assert result["feedback_count"] == MIN_FEEDBACK_FOR_REFLECTION
    assert result["proposal_count"] == 2
    statuses = {p.change_type: p.status for p in result["proposals"]}
    assert statuses["system_prompt"] == "pending"
    assert statuses["few_shot_example"] == "auto_applied"

    assert isinstance(captured["state"], AgentState)
    assert captured["config"].agent_name == "learning"
    assert captured["config"].task_type == "reflect"


async def test_run_reflection_handles_empty_proposal_list(db_session) -> None:
    for i in range(MIN_FEEDBACK_FOR_REFLECTION):
        await capture_feedback(db_session, task_id=f"t{i}", agent_name="content_writer", signal_type=SIGNAL_REJECTED, detail="x")

    llm_client = _make_llm_client(json.dumps([]))
    result = await run_reflection(db_session, llm_client)

    assert result["ran"] is True
    assert result["proposal_count"] == 0


async def test_run_reflection_raises_on_non_json_response(db_session) -> None:
    for i in range(MIN_FEEDBACK_FOR_REFLECTION):
        await capture_feedback(db_session, task_id=f"t{i}", agent_name="content_writer", signal_type=SIGNAL_REJECTED, detail="x")

    llm_client = _make_llm_client("not json")
    with pytest.raises(ValueError):
        await run_reflection(db_session, llm_client)


async def test_run_reflection_raises_when_response_is_not_a_json_array(db_session) -> None:
    for i in range(MIN_FEEDBACK_FOR_REFLECTION):
        await capture_feedback(db_session, task_id=f"t{i}", agent_name="content_writer", signal_type=SIGNAL_REJECTED, detail="x")

    llm_client = _make_llm_client(json.dumps({"not": "a list"}))
    with pytest.raises(ValueError, match="must be a JSON array"):
        await run_reflection(db_session, llm_client)


def test_system_prompt_is_registered_and_mentions_all_taught_change_types() -> None:
    assert get_prompt("learning") == REFLECTION_SYSTEM_PROMPT
    # "safety_threshold" is a defensive alias in ALWAYS_REVIEW_TYPES (matches
    # skills/LEARNING.md's naming) but deliberately isn't taught to the model
    # as a distinct category — it's redundant with confidence_threshold/
    # approval_gating_rule, which are taught.
    taught_types = (AUTO_APPLY_TYPES | ALWAYS_REVIEW_TYPES) - {"safety_threshold"}
    for change_type in taught_types:
        assert change_type in REFLECTION_SYSTEM_PROMPT
