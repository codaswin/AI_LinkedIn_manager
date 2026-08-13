"""Deterministic (non-LLM) eval metrics.

Two of the product spec's three declared metrics can be checked without a judge call:
escalation precision (compare expected vs actual routing decision) and a
`must_avoid` phrase check (golden-set-declared phrases a good draft should
never contain). The third — brand-voice fidelity / groundedness — needs
subjective judgment and lives in llm_judge.py instead.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    score: float
    details: str


def must_avoid_check(case_id: str, generated_text: str, must_avoid: list[str]) -> EvalResult:
    """Fails if any of the case's declared `must_avoid` phrases appear in the

    generated text (case-insensitive substring match). Deliberately simple —
    this is a cheap, deterministic backstop, not a substitute for the judge's
    subjective brand-voice/groundedness scoring.
    """
    lowered = generated_text.lower()
    hits = [phrase for phrase in must_avoid if phrase.lower() in lowered]
    passed = not hits
    return EvalResult(
        case_id=case_id,
        passed=passed,
        score=1.0 if passed else 0.0,
        details=f"matched forbidden phrase(s): {hits}" if hits else "clean",
    )


def escalation_precision(cases: list[dict]) -> dict:
    """Of cases that SHOULD escalate, how many did (recall)? Of cases that

    shouldn't, how many wrongly did (1 - precision)? Each case dict needs
    `expected_behavior` ("escalate" | "draft") and `actual_behavior` (same).
    """
    should_escalate = [c for c in cases if c["expected_behavior"] == "escalate"]
    correctly_escalated = sum(1 for c in should_escalate if c["actual_behavior"] == "escalate")
    should_not = [c for c in cases if c["expected_behavior"] != "escalate"]
    wrongly_escalated = sum(1 for c in should_not if c["actual_behavior"] == "escalate")

    recall = correctly_escalated / len(should_escalate) if should_escalate else 1.0
    precision = 1 - (wrongly_escalated / len(should_not)) if should_not else 1.0
    return {
        "recall": recall,
        "precision": precision,
        "should_escalate_count": len(should_escalate),
        "should_not_escalate_count": len(should_not),
        "wrongly_escalated_count": wrongly_escalated,
        "missed_escalation_count": len(should_escalate) - correctly_escalated,
    }


def aggregate_pass_rate(results: list[EvalResult]) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r.passed) / len(results)
