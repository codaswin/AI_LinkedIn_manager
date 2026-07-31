# Learning Skill

Feedback capture and a reflection loop — self-improvement that's reviewed, not silent drift.

## Feedback Capture
```python
# models/feedback.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class Feedback(Base):
    __tablename__ = "feedback"
    id = Column(Integer, primary_key=True)
    task_id = Column(String, nullable=False, index=True)
    signal_type = Column(String, nullable=False)  # "thumbs_up" | "thumbs_down" | "human_correction" | "escalation_outcome"
    detail = Column(String, nullable=True)         # e.g. the human's corrected answer
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# learning/feedback.py
from sqlalchemy.orm import Session
from app.models.feedback import Feedback


def capture_feedback(db: Session, task_id: str, signal_type: str, detail: str = ""):
    entry = Feedback(task_id=task_id, signal_type=signal_type, detail=detail)
    db.add(entry)
    db.commit()


def recent_negative_feedback(db: Session, days: int = 7) -> list[Feedback]:
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    return (
        db.query(Feedback)
        .filter(Feedback.signal_type.in_(["thumbs_down", "human_correction"]), Feedback.created_at >= cutoff)
        .all()
    )
```

## Reflection Job (periodic, not real-time)
```python
# learning/reflection_job.py
from app.learning.feedback import recent_negative_feedback
from app.learning.proposal_review import submit_proposal
from app.llmops.model_router import route_and_call_sync
from app.database import SessionLocal

REFLECTION_PROMPT = """You are analyzing recent negative feedback on an AI assistant to find patterns.

Feedback entries:
{entries}

Identify up to 3 concrete, specific patterns (not vague generalities), and for each propose ONE
targeted change. Categorize each proposed change as one of:
- "retrieval_weight" (safe to auto-apply if it's a numeric tuning)
- "few_shot_example" (safe to auto-apply, additive only)
- "system_prompt" (requires human review — always)
- "safety_threshold" (requires human review — always)

Respond in JSON: [{{"pattern": "...", "change_type": "...", "proposed_change": "...", "confidence": <0-1>}}]
"""


def run_reflection():
    db = SessionLocal()
    feedback = recent_negative_feedback(db)
    if len(feedback) < 5:
        return  # not enough signal to draw conclusions yet

    entries_text = "\n".join(f"- {f.signal_type}: {f.detail}" for f in feedback)
    proposals_raw = route_and_call_sync(task_type="evaluation", prompt=REFLECTION_PROMPT.format(entries=entries_text))
    proposals = parse_json(proposals_raw)

    for p in proposals:
        submit_proposal(p)


def parse_json(text: str) -> list[dict]:
    import json
    return json.loads(text)
```
Run via a scheduled job (Celery beat, cron) — weekly or after N new feedback entries, not on every request.

## Proposal Review Queue
```python
# learning/proposal_review.py
AUTO_APPLY_TYPES = {"retrieval_weight", "few_shot_example"}
ALWAYS_REVIEW_TYPES = {"system_prompt", "safety_threshold"}


def submit_proposal(proposal: dict):
    if proposal["change_type"] in ALWAYS_REVIEW_TYPES:
        _queue_for_human_review(proposal)
    elif proposal["change_type"] in AUTO_APPLY_TYPES and proposal["confidence"] >= 0.8:
        _apply_and_log(proposal)
    else:
        _queue_for_human_review(proposal)  # default to review when uncertain


def _queue_for_human_review(proposal: dict):
    # persist to a review table / notify via Slack / dashboard entry
    ...


def _apply_and_log(proposal: dict):
    # apply the numeric/additive change, and log it exactly like a manual change would be —
    # auto-applied does not mean unaudited
    ...
```

## Optional: Fine-tune Export
```python
# learning/finetune_export.py
def export_finetune_dataset(db, min_confidence: float = 0.9) -> str:
    """Export only high-confidence, human-reviewed interactions — never raw unreviewed logs."""
    ...
```

## Best Practices
- The reflection job runs on a schedule, never inline in the request path — self-improvement analysis is not latency-critical
- `system_prompt` and `safety_threshold` changes are ALWAYS human-reviewed, regardless of the model's confidence in its own proposal — the model proposing a change to its own safety threshold is exactly the case that must never auto-apply
- Every auto-applied change is logged with the same rigor as a manual one — "auto-applied" is not a reason to skip the audit trail
- Feed eval results back into this loop — a proposal that would regress the eval suite should be rejected, not just theoretically reasonable
