"""Durable, atomic human approval and controlled execution workflow."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import structlog
from app.models.approval_request import ApprovalExecutionAttemptRecord, ApprovalRequestRecord
from app.safety.kill_switch import is_system_paused
from app.tenancy.context import get_current_user_id
from app.tools.execution_context import idempotency_scope
from app.tools.registry import execute_tool, registry
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

ApprovalStatusLiteral = Literal["pending", "executing", "succeeded", "failed", "rejected"]


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tool_name: str
    arguments: dict[str, Any]
    requested_by_agent: str
    reason: str
    confidence: float | None = None
    status: ApprovalStatusLiteral
    idempotency_key: str
    attempt_count: int
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    execution_started_at: datetime | None = None
    execution_finished_at: datetime | None = None
    last_error: str | None = None


class SystemPausedError(RuntimeError):
    pass


class ApprovalRequestNotFoundError(RuntimeError):
    pass


class ApprovalRequestAlreadyDecidedError(RuntimeError):
    pass


def _max_attempts() -> int:
    return max(1, min(int(os.environ.get("APPROVAL_MAX_ATTEMPTS", "3")), 10))


def _execution_lease() -> timedelta:
    seconds = max(30, int(os.environ.get("APPROVAL_EXECUTION_LEASE_SECONDS", "900")))
    return timedelta(seconds=seconds)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def submit_for_approval(
    db: AsyncSession,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    requested_by_agent: str,
    reason: str,
    confidence: float | None = None,
) -> ApprovalRequest:
    try:
        gated = registry.requires_approval(tool_name)
    except KeyError as exc:
        raise ValueError(str(exc)) from exc
    if not gated:
        raise ValueError(
            f"'{tool_name}' is not registered with requires_approval=True in tools.registry; "
            "submit_for_approval must never be used to queue a non-gated tool call."
        )

    record = ApprovalRequestRecord(
        id=str(uuid.uuid4()),
        user_id=get_current_user_id(),
        tool_name=tool_name,
        arguments=arguments,
        requested_by_agent=requested_by_agent,
        reason=reason,
        confidence=confidence,
        status="pending",
        idempotency_key=str(uuid.uuid4()),
        attempt_count=0,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    logger.info(
        "approval_requested",
        approval_id=record.id,
        idempotency_key=record.idempotency_key,
        tool_name=tool_name,
        requested_by_agent=requested_by_agent,
        confidence=confidence,
    )
    return ApprovalRequest.model_validate(record)


async def _locked_record(db: AsyncSession, approval_id: str) -> ApprovalRequestRecord:
    result = await db.execute(
        select(ApprovalRequestRecord).where(ApprovalRequestRecord.id == approval_id).with_for_update()
    )
    record = result.scalar_one_or_none()
    # A mismatched owner reads identically to "doesn't exist" — never confirms
    # to a caller that some other user's approval request exists at all.
    if record is None or record.user_id != get_current_user_id():
        raise ApprovalRequestNotFoundError(f"No approval request with id {approval_id!r}")
    return record


async def _claim_execution(
    db: AsyncSession, approval_id: str, decided_by: str, allowed_status: str
) -> tuple[str, dict[str, Any], int, str]:
    record = await _locked_record(db, approval_id)
    now = datetime.now(timezone.utc)
    if record.status != allowed_status:
        stale_execution = (
            allowed_status == "failed"
            and record.status == "executing"
            and record.execution_started_at is not None
            and _as_utc(record.execution_started_at) <= now - _execution_lease()
        )
        if not stale_execution:
            raise ApprovalRequestAlreadyDecidedError(
                f"Approval request {approval_id!r} is '{record.status}', not {allowed_status}"
            )
        previous_result = await db.execute(
            select(ApprovalExecutionAttemptRecord).where(
                ApprovalExecutionAttemptRecord.approval_id == approval_id,
                ApprovalExecutionAttemptRecord.attempt_number == record.attempt_count,
            )
        )
        previous = previous_result.scalar_one_or_none()
        if previous is not None:
            previous.status = "failed"
            previous.finished_at = now
            previous.error = "Execution lease expired; outcome may be ambiguous"
        record.status = "failed"
        record.last_error = "Execution lease expired; outcome may be ambiguous"
        record.execution_finished_at = now
    if record.attempt_count >= _max_attempts():
        raise ApprovalRequestAlreadyDecidedError(
            f"Approval request {approval_id!r} exhausted its {_max_attempts()} execution attempts"
        )

    record.status = "executing"
    record.decided_by = decided_by
    record.decided_at = record.decided_at or now
    record.execution_started_at = now
    record.execution_finished_at = None
    record.last_error = None
    record.attempt_count += 1
    attempt = ApprovalExecutionAttemptRecord(
        id=str(uuid.uuid4()),
        approval_id=record.id,
        attempt_number=record.attempt_count,
        idempotency_key=record.idempotency_key,
        status="executing",
        started_at=now,
    )
    db.add(attempt)
    await db.commit()
    return record.tool_name, dict(record.arguments), record.attempt_count, record.idempotency_key


async def _finish_execution(
    db: AsyncSession,
    approval_id: str,
    attempt_number: int,
    result: dict[str, Any] | None,
    error: str | None,
) -> None:
    record = await _locked_record(db, approval_id)
    attempt_result = await db.execute(
        select(ApprovalExecutionAttemptRecord)
        .where(
            ApprovalExecutionAttemptRecord.approval_id == approval_id,
            ApprovalExecutionAttemptRecord.attempt_number == attempt_number,
        )
        .with_for_update()
    )
    attempt = attempt_result.scalar_one()
    now = datetime.now(timezone.utc)
    succeeded = error is None and result is not None and result.get("status") == "success"
    message = error or (None if succeeded else str((result or {}).get("error", "Tool execution failed")))
    record.status = "succeeded" if succeeded else "failed"
    record.execution_finished_at = now
    record.last_error = message
    attempt.status = record.status
    attempt.finished_at = now
    attempt.result = result
    attempt.error = message
    await db.commit()


async def _execute_claimed(db: AsyncSession, approval_id: str, decided_by: str, allowed_status: str) -> dict[str, Any]:
    if is_system_paused():
        raise SystemPausedError("System is paused via the kill switch; approval execution is disabled while paused.")
    tool_name, arguments, attempt_number, idempotency_key = await _claim_execution(
        db, approval_id, decided_by, allowed_status
    )
    logger.info(
        "approval_execution_started",
        approval_id=approval_id,
        idempotency_key=idempotency_key,
        attempt=attempt_number,
        tool_name=tool_name,
        decided_by=decided_by,
    )
    try:
        with idempotency_scope(idempotency_key):
            result = await execute_tool(tool_name, arguments, approved=True)
    except Exception as exc:
        await _finish_execution(db, approval_id, attempt_number, None, str(exc))
        logger.exception("approved_tool_execution_failed", approval_id=approval_id)
        raise
    await _finish_execution(db, approval_id, attempt_number, result, None)
    logger.info(
        "approved_tool_executed",
        approval_id=approval_id,
        attempt=attempt_number,
        result_status=result.get("status"),
    )
    return result


async def approve(db: AsyncSession, approval_id: str, decided_by: str) -> dict[str, Any]:
    return await _execute_claimed(db, approval_id, decided_by, "pending")


async def retry(db: AsyncSession, approval_id: str, decided_by: str) -> dict[str, Any]:
    """Retry an explicitly failed execution, subject to APPROVAL_MAX_ATTEMPTS."""
    return await _execute_claimed(db, approval_id, decided_by, "failed")


async def reject(db: AsyncSession, approval_id: str, decided_by: str, reason: str | None = None) -> ApprovalRequest:
    record = await _locked_record(db, approval_id)
    if record.status != "pending":
        raise ApprovalRequestAlreadyDecidedError(f"Approval request {approval_id!r} is '{record.status}', not pending")
    record.status = "rejected"
    record.decided_by = decided_by
    record.decided_at = datetime.now(timezone.utc)
    if reason:
        record.reason = f"{record.reason} | rejection reason: {reason}"
    await db.commit()
    await db.refresh(record)
    logger.info("approval_rejected", approval_id=approval_id, decided_by=decided_by, reason=reason)
    return ApprovalRequest.model_validate(record)


async def list_actionable(db: AsyncSession) -> list[ApprovalRequest]:
    result = await db.execute(
        select(ApprovalRequestRecord)
        .where(
            ApprovalRequestRecord.status.in_(("pending", "executing", "failed")),
            ApprovalRequestRecord.user_id == get_current_user_id(),
        )
        .order_by(ApprovalRequestRecord.created_at)
    )
    return [ApprovalRequest.model_validate(record) for record in result.scalars().all()]


async def list_pending(db: AsyncSession) -> list[ApprovalRequest]:
    result = await db.execute(
        select(ApprovalRequestRecord)
        .where(
            ApprovalRequestRecord.status == "pending",
            ApprovalRequestRecord.user_id == get_current_user_id(),
        )
        .order_by(ApprovalRequestRecord.created_at)
    )
    return [ApprovalRequest.model_validate(record) for record in result.scalars().all()]
