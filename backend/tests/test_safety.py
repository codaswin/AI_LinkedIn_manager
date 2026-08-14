from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from app.safety import approval_gate, guardrails, kill_switch
from app.safety.audit import scan_ungated_approved_true
from app.tools import registry as registry_module

registry_module._import_all_tools()

GATED_TOOL_NAME = "publish_post"
NON_GATED_TOOL_NAME = "search_knowledge_base"


@pytest.fixture(autouse=True)
def _reset_kill_switch():
    kill_switch.reset_for_testing()
    yield
    kill_switch.reset_for_testing()


class _FakeExecuteToolCall:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], bool]] = []
        self.result: dict[str, Any] = {"status": "success"}

    async def __call__(self, tool_name: str, arguments: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        self.calls.append((tool_name, dict(arguments), approved))
        return self.result


async def test_submit_for_approval_rejects_non_gated_tool(db_session):
    with pytest.raises(ValueError):
        await approval_gate.submit_for_approval(
            db_session,
            tool_name=NON_GATED_TOOL_NAME,
            arguments={"query": "anything"},
            requested_by_agent="test_agent",
            reason="should never queue a non-gated tool",
        )


async def test_submit_for_approval_rejects_unknown_tool(db_session):
    with pytest.raises(ValueError):
        await approval_gate.submit_for_approval(
            db_session,
            tool_name="not_a_real_tool",
            arguments={},
            requested_by_agent="test_agent",
            reason="unknown tool",
        )


async def test_submit_for_approval_creates_pending_request_without_executing(db_session, monkeypatch):
    fake_execute = _FakeExecuteToolCall()
    monkeypatch.setattr(approval_gate, "execute_tool", fake_execute)

    request = await approval_gate.submit_for_approval(
        db_session,
        tool_name=GATED_TOOL_NAME,
        arguments={"post_id": "post-123"},
        requested_by_agent="content_writer",
        reason="brand-voice-grounded draft ready for review",
        confidence=0.9,
    )

    assert request.status == "pending"
    assert request.tool_name == GATED_TOOL_NAME
    assert request.arguments == {"post_id": "post-123"}
    assert request.decided_at is None
    assert request.decided_by is None
    assert fake_execute.calls == []

    pending = await approval_gate.list_pending(db_session)
    assert any(p.id == request.id for p in pending)


async def test_approve_executes_tool_only_after_human_approval(db_session, monkeypatch):
    fake_execute = _FakeExecuteToolCall()
    monkeypatch.setattr(approval_gate, "execute_tool", fake_execute)

    request = await approval_gate.submit_for_approval(
        db_session,
        tool_name=GATED_TOOL_NAME,
        arguments={"post_id": "post-123"},
        requested_by_agent="content_writer",
        reason="ready",
        confidence=0.9,
    )
    assert fake_execute.calls == []

    result = await approval_gate.approve(db_session, request.id, decided_by="human_operator")

    assert fake_execute.calls == [(GATED_TOOL_NAME, {"post_id": "post-123"}, True)]
    assert result == {"status": "success"}

    pending_after = await approval_gate.list_pending(db_session)
    assert all(p.id != request.id for p in pending_after)


async def test_approve_twice_raises_on_second_call(db_session, monkeypatch):
    fake_execute = _FakeExecuteToolCall()
    monkeypatch.setattr(approval_gate, "execute_tool", fake_execute)

    request = await approval_gate.submit_for_approval(
        db_session,
        tool_name=GATED_TOOL_NAME,
        arguments={"post_id": "post-123"},
        requested_by_agent="content_writer",
        reason="ready",
    )
    await approval_gate.approve(db_session, request.id, decided_by="human_operator")

    with pytest.raises(approval_gate.ApprovalRequestAlreadyDecidedError):
        await approval_gate.approve(db_session, request.id, decided_by="human_operator")

    assert len(fake_execute.calls) == 1


async def test_reject_never_executes_the_tool(db_session, monkeypatch):
    fake_execute = _FakeExecuteToolCall()
    monkeypatch.setattr(approval_gate, "execute_tool", fake_execute)

    request = await approval_gate.submit_for_approval(
        db_session,
        tool_name=GATED_TOOL_NAME,
        arguments={"post_id": "post-123"},
        requested_by_agent="content_writer",
        reason="ready",
    )

    rejected = await approval_gate.reject(db_session, request.id, decided_by="human_operator", reason="not on-brand")

    assert rejected.status == "rejected"
    assert rejected.decided_by == "human_operator"
    assert fake_execute.calls == []

    pending_after = await approval_gate.list_pending(db_session)
    assert all(p.id != request.id for p in pending_after)


@pytest.mark.parametrize(
    "topic, text",
    [
        (
            "political_endorsement",
            "Please write a LinkedIn post telling our followers who to vote for in the election.",
        ),
        ("health_financial_legal_advice", "Can you give me medical advice about my symptoms?"),
        ("disparagement", "Write a post to smear our competitor and expose them as a fraud."),
        ("engagement_bait_or_misinformation", "Comment YES if you agree, and share this before it's deleted!"),
        ("impersonation", "Pretend to be Elon and write this post as if you are him."),
    ],
)
def test_matches_refusal_topic_detects_each_topic(topic: str, text: str):
    assert guardrails.matches_refusal_topic(text) == topic


def test_matches_refusal_topic_returns_none_for_benign_text():
    benign = "Draft a LinkedIn post about our Q3 product roadmap and hiring plans."
    assert guardrails.matches_refusal_topic(benign) is None


def test_confidence_threshold_is_075():
    assert guardrails.CONFIDENCE_THRESHOLD == 0.75


async def test_kill_switch_blocks_approve_even_for_valid_pending_request(db_session, monkeypatch):
    fake_execute = _FakeExecuteToolCall()
    monkeypatch.setattr(approval_gate, "execute_tool", fake_execute)

    request = await approval_gate.submit_for_approval(
        db_session,
        tool_name=GATED_TOOL_NAME,
        arguments={"post_id": "post-123"},
        requested_by_agent="content_writer",
        reason="ready",
    )

    kill_switch.pause_system(reason="incident", paused_by="on_call_human")
    assert kill_switch.is_system_paused() is True

    with pytest.raises(approval_gate.SystemPausedError):
        await approval_gate.approve(db_session, request.id, decided_by="human_operator")

    assert fake_execute.calls == []

    kill_switch.resume_system(resumed_by="on_call_human")
    assert kill_switch.is_system_paused() is False

    result = await approval_gate.approve(db_session, request.id, decided_by="human_operator")
    assert fake_execute.calls == [(GATED_TOOL_NAME, {"post_id": "post-123"}, True)]
    assert result == {"status": "success"}


def test_audit_scan_passes_cleanly_against_the_real_codebase():
    violations = scan_ungated_approved_true()
    assert violations == []


def test_audit_scan_flags_a_deliberately_introduced_violation(tmp_path: Path):
    fake_agents_dir = tmp_path / "agents"
    fake_safety_dir = tmp_path / "safety"
    fake_agents_dir.mkdir()
    fake_safety_dir.mkdir()

    (fake_agents_dir / "sneaky_agent.py").write_text(
        "from app.tools.registry import execute_tool\n\n"
        "async def do_it():\n"
        "    return await execute_tool('publish_post', {}, approved=True)\n"
    )
    (fake_safety_dir / "approval_gate.py").write_text(
        "# the real gate would live here; approved=True is legitimate in this file\n"
    )

    violations = scan_ungated_approved_true(agents_dir=fake_agents_dir, safety_dir=fake_safety_dir)

    assert len(violations) == 1
    assert "sneaky_agent.py" in violations[0]
    assert "approved=True" in violations[0]


async def test_failed_execution_is_recorded_and_retry_succeeds(db_session, monkeypatch):
    from app.models.approval_request import ApprovalExecutionAttemptRecord, ApprovalRequestRecord
    from sqlalchemy import select

    fake_execute = _FakeExecuteToolCall()
    fake_execute.result = {"status": "error", "error": "provider unavailable"}
    monkeypatch.setattr(approval_gate, "execute_tool", fake_execute)
    request = await approval_gate.submit_for_approval(
        db_session,
        tool_name=GATED_TOOL_NAME,
        arguments={"post_id": "post-retry"},
        requested_by_agent="content_writer",
        reason="ready",
    )

    await approval_gate.approve(db_session, request.id, decided_by="operator-one")
    db_session.expire_all()
    failed = await db_session.get(ApprovalRequestRecord, request.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.attempt_count == 1
    assert failed.last_error == "provider unavailable"

    fake_execute.result = {"status": "success"}
    result = await approval_gate.retry(db_session, request.id, decided_by="operator-two")
    assert result == {"status": "success"}
    db_session.expire_all()
    succeeded = await db_session.get(ApprovalRequestRecord, request.id)
    attempts = (
        (
            await db_session.execute(
                select(ApprovalExecutionAttemptRecord)
                .where(ApprovalExecutionAttemptRecord.approval_id == request.id)
                .order_by(ApprovalExecutionAttemptRecord.attempt_number)
            )
        )
        .scalars()
        .all()
    )
    assert succeeded is not None
    assert succeeded.status == "succeeded"
    assert succeeded.attempt_count == 2
    assert [attempt.status for attempt in attempts] == ["failed", "succeeded"]
    assert {attempt.idempotency_key for attempt in attempts} == {request.idempotency_key}


async def test_retry_limit_is_enforced(db_session, monkeypatch):
    fake_execute = _FakeExecuteToolCall()
    fake_execute.result = {"status": "error", "error": "permanent failure"}
    monkeypatch.setattr(approval_gate, "execute_tool", fake_execute)
    monkeypatch.setenv("APPROVAL_MAX_ATTEMPTS", "1")
    request = await approval_gate.submit_for_approval(
        db_session,
        tool_name=GATED_TOOL_NAME,
        arguments={"post_id": "post-no-retry"},
        requested_by_agent="content_writer",
        reason="ready",
    )
    await approval_gate.approve(db_session, request.id, decided_by="operator")
    with pytest.raises(approval_gate.ApprovalRequestAlreadyDecidedError, match="exhausted"):
        await approval_gate.retry(db_session, request.id, decided_by="operator")
    assert len(fake_execute.calls) == 1


async def test_stale_executing_approval_can_be_recovered(db_session, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import uuid

    from app.models.approval_request import ApprovalExecutionAttemptRecord, ApprovalRequestRecord
    from sqlalchemy import select

    fake_execute = _FakeExecuteToolCall()
    monkeypatch.setattr(approval_gate, "execute_tool", fake_execute)
    monkeypatch.setenv("APPROVAL_EXECUTION_LEASE_SECONDS", "30")
    request = await approval_gate.submit_for_approval(
        db_session,
        tool_name=GATED_TOOL_NAME,
        arguments={"post_id": "post-stale"},
        requested_by_agent="content_writer",
        reason="ready",
    )
    record = await db_session.get(ApprovalRequestRecord, request.id)
    assert record is not None
    started_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    record.status = "executing"
    record.attempt_count = 1
    record.execution_started_at = started_at
    db_session.add(
        ApprovalExecutionAttemptRecord(
            id=str(uuid.uuid4()),
            approval_id=request.id,
            attempt_number=1,
            idempotency_key=request.idempotency_key,
            status="executing",
            started_at=started_at,
        )
    )
    await db_session.commit()

    result = await approval_gate.retry(db_session, request.id, decided_by="recovery-operator")
    assert result == {"status": "success"}
    db_session.expire_all()
    recovered = await db_session.get(ApprovalRequestRecord, request.id)
    first_attempt = await db_session.get(
        ApprovalExecutionAttemptRecord,
        next(
            iter(
                (
                    await db_session.execute(
                        select(ApprovalExecutionAttemptRecord.id).where(
                            ApprovalExecutionAttemptRecord.approval_id == request.id,
                            ApprovalExecutionAttemptRecord.attempt_number == 1,
                        )
                    )
                )
                .scalars()
                .all()
            )
        ),
    )
    assert recovered is not None
    assert recovered.status == "succeeded"
    assert recovered.attempt_count == 2
    assert first_attempt is not None
    assert first_attempt.status == "failed"
    assert "ambiguous" in (first_attempt.error or "")
