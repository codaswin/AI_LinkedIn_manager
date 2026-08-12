"""Input/output guardrails: refusal-topic detection and human escalation.

Refusal topics are the 5 named in the PRP's SAFETY REQUIREMENTS section:
political endorsements, health/financial/legal advice, disparagement of a
named individual or competitor, engagement-bait/misinformation, and
authorship-misrepresenting impersonation. Matching is curated keyword/regex
per topic — a real classifier is a documented future refinement (see
skills/SAFETY.md Best Practices), not something this MVP scaffold attempts,
but the patterns below are real enough to catch obvious cases, not a stub
that always returns None.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

CONFIDENCE_THRESHOLD: float = 0.75

_RAW_REFUSAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "political_endorsement": (
        r"\bvote for\b",
        r"\bwho (should|do) (i|we) (vote for|support in the election)\b",
        r"\bendorse [a-z]+ for (president|senate|governor|congress|mayor)\b",
        r"\bwhich (candidate|party) should (i|we) (support|endorse|back)\b",
        r"\bwrite a post endorsing\b",
    ),
    "health_financial_legal_advice": (
        r"\b(medical|legal|financial) advice\b",
        r"\bshould i (invest in|sue|take|stop taking)\b",
        r"\bdiagnos(e|is) my\b",
        r"\bwhat medication should i\b",
        r"\bis it legal to\b",
        r"\bwhich stock should i buy\b",
        r"\btax advice\b",
        r"\bsymptoms of\b.*\bdo i have\b",
    ),
    "disparagement": (
        r"\btrash talk\b",
        r"\bsmear (campaign|our competitor|them)\b",
        r"\bexpose (them|him|her|[a-z]+) as (a )?fraud\b",
        r"\bcall out [a-z]+ by name\b",
        r"\b(worst|incompetent|clueless) (ceo|founder|company)\b",
        r"\bbash (our|the) competitor\b",
        r"\broast (our|the) competitor\b",
    ),
    "engagement_bait_or_misinformation": (
        r"\bcomment (yes|no|\"?\+1\"?) if\b",
        r"\blike (this |it )?if you agree\b",
        r"\bshare (this )?before (it'?s|it is) deleted\b",
        r"\btag someone who\b",
        r"\bdoctors hate (this|him|her)\b",
        r"\bthis (one weird trick|cures)\b",
        r"\bthey don'?t want you to know\b",
        r"\bunverified claim\b",
        r"\bmake it go viral\b",
    ),
    "impersonation": (
        r"\bwrite (this|it) as if (i am|you are)\b",
        r"\bpretend to be [a-z]+\b",
        r"\bpost this under (someone else'?s|another) name\b",
        r"\bimpersonat(e|ing)\b",
        r"\bghostwrite this as [a-z]+\b",
        r"\bsign (this|it) as if it'?s from\b",
        r"\bwrite in the voice of [a-z]+ and post it as them\b",
    ),
}

REFUSAL_TOPICS: dict[str, tuple[re.Pattern[str], ...]] = {
    topic: tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)
    for topic, patterns in _RAW_REFUSAL_PATTERNS.items()
}


def matches_refusal_topic(text: str) -> str | None:
    """Returns the matched topic name if `text` touches a refusal topic, else None."""
    for topic, patterns in REFUSAL_TOPICS.items():
        for pattern in patterns:
            if pattern.search(text):
                return topic
    return None


_ESCALATIONS: list[dict[str, Any]] = []


def escalate_to_human(*, agent_name: str, reason: str, context: dict[str, Any]) -> None:
    """Record that a human must handle something manually.

    Distinct from the approval queue in approval_gate.py: there is no draft
    tool call to approve/reject here (e.g. a refusal-topic hit, a confidence
    escalation with no ready draft) — just something a human needs to
    resolve by hand.
    """
    record = {
        "agent_name": agent_name,
        "reason": reason,
        "context": context,
        "escalated_at": datetime.now(timezone.utc),
    }
    _ESCALATIONS.append(record)
    logger.warning("escalated_to_human", agent_name=agent_name, reason=reason, context=context)


def list_escalations() -> list[dict[str, Any]]:
    return list(_ESCALATIONS)


def clear_escalations_for_testing() -> None:
    """Test-only — production code must never call this."""
    _ESCALATIONS.clear()
