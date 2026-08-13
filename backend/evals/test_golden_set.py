"""Validates the golden sets themselves — schema, minimum size, and (for

replies) consistency with the real refusal-topic detector. These are
starter/placeholder sets (see run_evals.py's module docstring) pending the
user's real curated 50-post/30-reply set from their own posting history —
this file guards the *shape* stays right regardless of who authored the data.
"""

from __future__ import annotations

from app.safety.guardrails import matches_refusal_topic
from evals.run_evals import DEFAULT_POSTS_PATH, DEFAULT_REPLIES_PATH, load_jsonl

# eval-agent.md's review checklist: "enough examples per category to be
# statistically meaningful (not 3 examples for a production-grade claim)".
# These are lower than the product spec's target (50 posts / 30 replies) because this
# is starter data, not the user's real curated set — but still enough to be
# a meaningful smoke test, not a token gesture.
_MIN_POST_CASES = 15
_MIN_REPLY_CASES = 12

_VALID_FORMATS = {"text", "article", "poll"}
_VALID_BEHAVIORS = {"draft", "escalate"}


def test_golden_set_posts_meets_minimum_size() -> None:
    cases = load_jsonl(DEFAULT_POSTS_PATH)
    assert len(cases) >= _MIN_POST_CASES


def test_golden_set_posts_have_required_fields_and_unique_ids() -> None:
    cases = load_jsonl(DEFAULT_POSTS_PATH)
    seen_ids = set()
    for case in cases:
        for field in ("id", "topic", "angle", "format", "category"):
            assert field in case, f"{case.get('id', '?')} missing required field {field!r}"
        assert case["format"] in _VALID_FORMATS, f"{case['id']}: invalid format {case['format']!r}"
        assert case["id"] not in seen_ids, f"duplicate id: {case['id']}"
        seen_ids.add(case["id"])


def test_golden_set_posts_have_at_least_one_must_avoid_case() -> None:
    """Every case should declare at least one must_avoid phrase — a case with

    none can never fail the deterministic must_avoid_check, which would
    silently make it a no-op in the pass-rate metric.
    """
    cases = load_jsonl(DEFAULT_POSTS_PATH)
    with_must_avoid = [c for c in cases if c.get("must_avoid")]
    assert len(with_must_avoid) == len(cases)


def test_golden_set_replies_meets_minimum_size() -> None:
    cases = load_jsonl(DEFAULT_REPLIES_PATH)
    assert len(cases) >= _MIN_REPLY_CASES


def test_golden_set_replies_have_required_fields_and_unique_ids() -> None:
    cases = load_jsonl(DEFAULT_REPLIES_PATH)
    seen_ids = set()
    for case in cases:
        for field in ("id", "notification", "expected_behavior", "category"):
            assert field in case, f"{case.get('id', '?')} missing required field {field!r}"
        assert case["expected_behavior"] in _VALID_BEHAVIORS
        assert case["notification"].get("type") in {"comment", "dm", "connection_request"}
        assert case["notification"].get("text")
        assert case["id"] not in seen_ids, f"duplicate id: {case['id']}"
        seen_ids.add(case["id"])


def test_golden_set_replies_covers_both_behaviors() -> None:
    cases = load_jsonl(DEFAULT_REPLIES_PATH)
    behaviors = {c["expected_behavior"] for c in cases}
    assert behaviors == _VALID_BEHAVIORS


def test_golden_set_replies_marked_matches_refusal_keyword_actually_match() -> None:
    """Regression guard: a case claiming `matches_refusal_keyword: true` must

    genuinely trip the real guardrails function, and one claiming false must
    genuinely not — otherwise this golden set would silently stop testing
    the real safety layer and only test LLM judgment.
    """
    cases = load_jsonl(DEFAULT_REPLIES_PATH)
    for case in cases:
        if "matches_refusal_keyword" not in case:
            continue
        matched = matches_refusal_topic(case["notification"]["text"]) is not None
        assert matched == case["matches_refusal_keyword"], (
            f"{case['id']}: matches_refusal_keyword={case['matches_refusal_keyword']} "
            f"but matches_refusal_topic() returned match={matched}"
        )


def test_golden_set_replies_escalate_cases_are_mostly_keyword_verifiable() -> None:
    """Not a hard 100% requirement (some cases deliberately test LLM judgment

    beyond the keyword backstop — see reply-015), but most "escalate" cases
    should be independently verifiable against the real safety layer, not
    rely solely on trusting the LLM's own judgment.
    """
    cases = load_jsonl(DEFAULT_REPLIES_PATH)
    escalate_cases = [c for c in cases if c["expected_behavior"] == "escalate"]
    keyword_verified = [c for c in escalate_cases if c.get("matches_refusal_keyword")]
    assert len(keyword_verified) / len(escalate_cases) >= 0.5
