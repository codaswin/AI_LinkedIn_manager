"""The human-approval workflow around tools.registry.execute_tool's low-level gate.

project invariant #3: any `requires_approval` tool blocks until a human
approves — no exceptions. `approve()` below is THE ONLY function in this
codebase permitted to call `tools.registry.execute_tool(..., approved=True)`;
`audit.py` statically scans for that literal to enforce it stays that way.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import structlog
from app.models.approval_request import ApprovalRequestRecord
from app.safety.kill_switch import is_system_paused
from app.tools.registry import execute_tool, registry
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

ApprovalStatusLiteral = Literal["pending", "approved", "rejected"]


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tool_name: str
    arguments: dict[str, Any]
    requested_by_agent: str
    reason: str
    confidence: float | None = None
    status: ApprovalStatusLiteral
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None


class SystemPausedError(RuntimeError):
    """Raised by approve() when the kill switch is active (safety/kill_switch.py)."""


class ApprovalRequestNotFoundError(RuntimeError):
    pass


class ApprovalRequestAlreadyDecidedError(RuntimeError):
    pass


async def submit_for_approval(
    db: AsyncSession,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    requested_by_agent: str,
    reason: str,
    confidence: float | None = None,
) -> ApprovalRequest:
    """Queue a requires_approval tool call for human review.

    Never executes anything itself — it only ever writes a pending record.
    """
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
        tool_name=tool_name,
        arguments=arguments,
        requested_by_agent=requested_by_agent,
        reason=reason,
        confidence=confidence,
        status="pending",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    logger.info(
        "approval_requested",
        approval_id=record.id,
        tool_name=tool_name,
        requested_by_agent=requested_by_agent,
        confidence=confidence,
    )
    return ApprovalRequest.model_validate(record)


async def _get_pending_or_raise(db: AsyncSession, approval_id: str) -> ApprovalRequestRecord:
    record = await db.get(ApprovalRequestRecord, approval_id)
    if record is None:
        raise ApprovalRequestNotFoundError(f"No approval request with id {approval_id!r}")
    if record.status != "pending":
        raise ApprovalRequestAlreadyDecidedError(
            f"Approval request {approval_id!r} is already '{record.status}', not pending"
        )
    return record


async def approve(db: AsyncSession, approval_id: str, decided_by: str) -> dict[str, Any]:
    """Human approves: marks the request approved, executes the tool, returns its result.

    This is THE ONLY function in the entire codebase permitted to call
    tools.registry.execute_tool(tool_name, arguments, approved=True).
    """
    if is_system_paused():
        raise SystemPausedError(
            "System is paused via the kill switch; approve() refuses to execute anything while paused."
        )

    record = await _get_pending_or_raise(db, approval_id)

    record.status = "approved"
    record.decided_by = decided_by
    record.decided_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info(
        "approval_granted",
        approval_id=approval_id,
        tool_name=record.tool_name,
        decided_by=decided_by,
    )

    result = await execute_tool(record.tool_name, record.arguments, approved=True)

    logger.info(
        "approved_tool_executed",
        approval_id=approval_id,
        tool_name=record.tool_name,
        result_status=result.get("status"),
    )
    return result


async def reject(
    db: AsyncSession, approval_id: str, decided_by: str, reason: str | None = None
) -> ApprovalRequest:
    """Human rejects: marks rejected, never executes anything."""
    record = await _get_pending_or_raise(db, approval_id)

    record.status = "rejected"
    record.decided_by = decided_by
    record.decided_at = datetime.now(timezone.utc)
    if reason:
        record.reason = f"{record.reason} | rejection reason: {reason}"
    await db.commit()
    await db.refresh(record)

    logger.info("approval_rejected", approval_id=approval_id, decided_by=decided_by, reason=reason)
    return ApprovalRequest.model_validate(record)


async def list_pending(db: AsyncSession) -> list[ApprovalRequest]:
    """All approval requests awaiting a human decision."""
    result = await db.execute(
        select(ApprovalRequestRecord).where(ApprovalRequestRecord.status == "pending")
    )
    records = result.scalars().all()
    return [ApprovalRequest.model_validate(record) for record in records]
