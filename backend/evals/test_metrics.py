from __future__ import annotations

from evals.metrics import (
    EvalResult,
    aggregate_pass_rate,
    escalation_precision,
    must_avoid_check,
)


def test_must_avoid_check_passes_when_no_forbidden_phrase_present() -> None:
    result = must_avoid_check("c1", "A grounded, specific post about agent frameworks.", ["generic AI-blog tone"])
    assert result.passed is True
    assert result.score == 1.0


def test_must_avoid_check_fails_on_case_insensitive_match() -> None:
    result = must_avoid_check("c1", "This is GENERIC ai-blog TONE content.", ["generic AI-blog tone"])
    assert result.passed is False
    assert result.score == 0.0
    assert "generic AI-blog tone" in result.details


def test_must_avoid_check_reports_all_matched_phrases() -> None:
    result = must_avoid_check("c1", "unverified performance claims and generic AI-blog tone", ["unverified performance claims", "generic AI-blog tone", "not present"])
    assert result.passed is False
    assert "unverified performance claims" in result.details
    assert "generic AI-blog tone" in result.details
    assert "not present" not in result.details


def test_escalation_precision_perfect_score() -> None:
    cases = [
        {"expected_behavior": "escalate", "actual_behavior": "escalate"},
        {"expected_behavior": "draft", "actual_behavior": "draft"},
    ]
    result = escalation_precision(cases)
    assert result["recall"] == 1.0
    assert result["precision"] == 1.0


def test_escalation_precision_missed_escalation_hurts_recall_not_precision() -> None:
    cases = [
        {"expected_behavior": "escalate", "actual_behavior": "draft"},
        {"expected_behavior": "draft", "actual_behavior": "draft"},
    ]
    result = escalation_precision(cases)
    assert result["recall"] == 0.0
    assert result["precision"] == 1.0
    assert result["missed_escalation_count"] == 1


def test_escalation_precision_over_escalation_hurts_precision_not_recall() -> None:
    cases = [
        {"expected_behavior": "escalate", "actual_behavior": "escalate"},
        {"expected_behavior": "draft", "actual_behavior": "escalate"},
    ]
    result = escalation_precision(cases)
    assert result["recall"] == 1.0
    assert result["precision"] == 0.0
    assert result["wrongly_escalated_count"] == 1


def test_escalation_precision_handles_empty_categories_gracefully() -> None:
    all_escalate = [{"expected_behavior": "escalate", "actual_behavior": "escalate"}]
    result = escalation_precision(all_escalate)
    assert result["precision"] == 1.0  # no should-not-escalate cases -> vacuously perfect

    all_draft = [{"expected_behavior": "draft", "actual_behavior": "draft"}]
    result = escalation_precision(all_draft)
    assert result["recall"] == 1.0  # no should-escalate cases -> vacuously perfect


def test_aggregate_pass_rate() -> None:
    results = [
        EvalResult(case_id="1", passed=True, score=1.0, details=""),
        EvalResult(case_id="2", passed=True, score=1.0, details=""),
        EvalResult(case_id="3", passed=False, score=0.0, details=""),
        EvalResult(case_id="4", passed=False, score=0.0, details=""),
    ]
    assert aggregate_pass_rate(results) == 0.5


def test_aggregate_pass_rate_empty_list() -> None:
    assert aggregate_pass_rate([]) == 0.0
