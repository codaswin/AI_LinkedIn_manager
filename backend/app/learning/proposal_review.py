"""Human-review queue for the reflection job's proposals.

Non-negotiable (agents/learning-agent.md): any proposed change that touches
a system prompt, a tool definition, or a safety threshold goes into the
human-approval queue — it never auto-applies, regardless of how confident
the reflection job is. That rule is enforced here, unconditionally, in
submit_proposal() itself: ALWAYS_REVIEW_TYPES membership always wins over
confidence, full stop.

Scope note on "auto_applied": no retrieval-weight-tuning mechanism exists
anywhere in rag/ yet (no tunable config to mutate), so "auto_applied" here
means exactly what it can honestly mean today — the proposal is recorded as
decided, with who/when, the same audit rigor a manual decision gets
(skills/LEARNING.md: "auto-applied does not mean unaudited"). Actually
mutating ranking weights or injecting a few-shot example is future work for
whichever module owns that config, not invented here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

import structlog
from app.models.learning_proposal import LearningProposalRecord
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# product spec self-learning scope, verbatim:
# "What improves automatically: Retrieval ranking weights in the RAG index;
#  few-shot examples pulled from top-performing past posts."
AUTO_APPLY_TYPES = {"retrieval_weight", "few_shot_example"}

# product spec self-learning scope, verbatim: "What requires human review
# before deploying: Any change to the system prompt or brand-voice profile;
# any new tool; any change to approval-gating rules or confidence
# thresholds." "safety_threshold" is kept as an alias since that's the exact
# category name skills/LEARNING.md's reflection prompt uses.
ALWAYS_REVIEW_TYPES = {
    "system_prompt",
    "brand_voice_profile",
    "new_tool",
    "approval_gating_rule",
    "confidence_threshold",
    "safety_threshold",
}

# A numeric/additive proposal still needs to clear this bar to auto-apply;
# below it, or any change_type not in AUTO_APPLY_TYPES, defaults to human
# review (skills/LEARNING.md: "default to review when uncertain").
AUTO_APPLY_CONFIDENCE_THRESHOLD = 0.8

ProposalStatusLiteral = Literal["pending", "approved", "rejected", "auto_applied"]


class LearningProposal(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    pattern: str
    change_type: str
    proposed_change: str
    confidence: float
    status: ProposalStatusLiteral
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None


class ProposalNotFoundError(RuntimeError):
    pass


class ProposalAlreadyDecidedError(RuntimeError):
    pass


async def submit_proposal(
    db: AsyncSession,
    *,
    pattern: str,
    change_type: str,
    proposed_change: str,
    confidence: float,
) -> LearningProposal:
    """Classify and route one reflection-job proposal.

    ALWAYS_REVIEW_TYPES always queues for human review, unconditionally —
    checked first, before confidence is even considered. Everything else
    (including an unrecognized change_type) also defaults to human review;
    only AUTO_APPLY_TYPES at or above AUTO_APPLY_CONFIDENCE_THRESHOLD skip it.
    """
    if change_type in ALWAYS_REVIEW_TYPES:
        status: ProposalStatusLiteral = "pending"
    elif change_type in AUTO_APPLY_TYPES and confidence >= AUTO_APPLY_CONFIDENCE_THRESHOLD:
        status = "auto_applied"
    else:
        status = "pending"

    record = LearningProposalRecord(
        id=str(uuid.uuid4()),
        pattern=pattern,
        change_type=change_type,
        proposed_change=proposed_change,
        confidence=confidence,
        status=status,
    )
    if status == "auto_applied":
        record.decided_at = datetime.now(timezone.utc)
        record.decided_by = "system:reflection_job"

    db.add(record)
    await db.commit()
    await db.refresh(record)

    logger.info(
        "learning_proposal_auto_applied" if status == "auto_applied" else "learning_proposal_submitted",
        proposal_id=record.id,
        change_type=change_type,
        confidence=confidence,
        status=status,
    )
    return LearningProposal.model_validate(record)


async def _get_pending_or_raise(db: AsyncSession, proposal_id: str) -> LearningProposalRecord:
    record = await db.get(LearningProposalRecord, proposal_id)
    if record is None:
        raise ProposalNotFoundError(f"No learning proposal with id {proposal_id!r}")
    if record.status != "pending":
        raise ProposalAlreadyDecidedError(
            f"Learning proposal {proposal_id!r} is already {record.status!r}, not pending"
        )
    return record


async def approve_proposal(db: AsyncSession, proposal_id: str, decided_by: str) -> LearningProposal:
    """Human approves: marks approved. Does not itself implement the change —

    a system_prompt/brand_voice_profile/etc. change is applied by a human
    editing the relevant file, same as any other manual change; approving
    here is a sign-off, not a code-mutation trigger.
    """
    record = await _get_pending_or_raise(db, proposal_id)
    record.status = "approved"
    record.decided_by = decided_by
    record.decided_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(record)

    logger.info("learning_proposal_approved", proposal_id=proposal_id, decided_by=decided_by)
    return LearningProposal.model_validate(record)


async def reject_proposal(
    db: AsyncSession, proposal_id: str, decided_by: str, reason: str | None = None
) -> LearningProposal:
    record = await _get_pending_or_raise(db, proposal_id)
    record.status = "rejected"
    record.decided_by = decided_by
    record.decided_at = datetime.now(timezone.utc)
    if reason:
        record.proposed_change = f"{record.proposed_change} | rejection reason: {reason}"
    await db.commit()
    await db.refresh(record)

    logger.info("learning_proposal_rejected", proposal_id=proposal_id, decided_by=decided_by, reason=reason)
    return LearningProposal.model_validate(record)


async def list_pending(db: AsyncSession) -> list[LearningProposal]:
    result = await db.execute(select(LearningProposalRecord).where(LearningProposalRecord.status == "pending"))
    return [LearningProposal.model_validate(r) for r in result.scalars().all()]
