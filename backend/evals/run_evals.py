"""Eval runner + regression gate (validation gate 3).

Run as `python -m backend.evals.run_evals --compare-to-baseline` from the
repo root (mirrors safety/audit.py's sys.path bootstrap, needed for the
same reason: `python -m backend.evals.run_evals` doesn't put `backend/` on
sys.path by default, so `app.*`/`evals.*` imports would otherwise fail).

Both run_post_evals() and run_reply_evals() take an injectable `llm_client`
(harness.loop.LLMClient) — this file talks to a model exactly like every
runtime agent does, through content_writer.write_post()/
engagement.handle_notification(), which themselves only ever call the model
via harness.loop.run_step(). No live model integration exists yet anywhere
in this codebase (harness/loop.py's own docstring: "does not talk to a real
model yet"), so `pytest backend/evals` always injects a fake client — this
runner is what will exercise a real one once llmops wires route_and_call in.
"""

from __future__ import annotations

import json
import sys
import typing as t
from pathlib import Path

# `python -m backend.evals.run_evals` run from the repo root does not have
# `backend/` on sys.path (only the repo root itself resolves) — needed
# before the `app.*`/`evals.*` imports below.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.harness.loop import LLMClient
from evals.llm_judge import judge_post, judge_reply
from evals.metrics import (
    EvalResult,
    aggregate_pass_rate,
    escalation_precision,
    must_avoid_check,
)

_EVALS_DIR = Path(__file__).resolve().parent
DEFAULT_POSTS_PATH = _EVALS_DIR / "golden_set_posts.jsonl"
DEFAULT_REPLIES_PATH = _EVALS_DIR / "golden_set_replies.jsonl"
DEFAULT_BASELINE_PATH = _EVALS_DIR / "baseline.json"

WriteFn = t.Callable[..., t.Awaitable[dict[str, t.Any]]]
HandleNotificationFn = t.Callable[..., t.Awaitable[dict[str, t.Any]]]


class RegressionError(AssertionError):
    """Raised by compare_to_baseline() when a metric drops beyond the threshold."""


def load_jsonl(path: Path) -> list[dict[str, t.Any]]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


async def _null_submit_approval_fn(db: t.Any, **kwargs: t.Any) -> dict[str, t.Any]:
    """Default submit_approval_fn for eval runs: captures the arguments a real

    approval-gate submission would carry, without needing a live DB. The
    eval harness cares about what WOULD have been submitted (the draft
    text), not about actually queuing it.
    """
    return {"status": "pending_eval_only", "arguments": kwargs.get("arguments", {})}


def _avg(dicts: list[dict[str, t.Any]], key: str) -> float | None:
    values = [float(d[key]) for d in dicts if isinstance(d.get(key), (int, float))]
    return sum(values) / len(values) if values else None


async def run_post_evals(
    llm_client: LLMClient,
    write_post_fn: WriteFn,
    posts_path: Path = DEFAULT_POSTS_PATH,
) -> dict[str, t.Any]:
    cases = load_jsonl(posts_path)
    must_avoid_results: list[EvalResult] = []
    judge_scores: list[dict[str, t.Any]] = []
    per_case: list[dict[str, t.Any]] = []

    for case in cases:
        brief = {
            "topic": case["topic"],
            "angle": case["angle"],
            "format": case["format"],
            "target_publish_date": case.get("target_publish_date"),
        }
        output = await write_post_fn(
            brief, llm_client=llm_client, submit_approval_fn=_null_submit_approval_fn
        )
        post_content = output.get("post_content", "")

        avoid_result = must_avoid_check(case["id"], post_content, case.get("must_avoid", []))
        must_avoid_results.append(avoid_result)

        judge = await judge_post(case, post_content, llm_client)
        judge_scores.append(judge)

        per_case.append(
            {
                "case_id": case["id"],
                "category": case["category"],
                "needs_human_rewrite": output.get("needs_human_rewrite"),
                "confidence": output.get("confidence"),
                "must_avoid_passed": avoid_result.passed,
                "judge": judge,
            }
        )

    return {
        "case_count": len(cases),
        "must_avoid_pass_rate": aggregate_pass_rate(must_avoid_results),
        "avg_brand_voice_fidelity": _avg(judge_scores, "brand_voice_fidelity"),
        "avg_groundedness": _avg(judge_scores, "groundedness"),
        "per_case": per_case,
    }


async def run_reply_evals(
    llm_client: LLMClient,
    handle_notification_fn: HandleNotificationFn,
    replies_path: Path = DEFAULT_REPLIES_PATH,
) -> dict[str, t.Any]:
    cases = load_jsonl(replies_path)
    judge_scores: list[dict[str, t.Any]] = []
    scored_cases: list[dict[str, t.Any]] = []

    for case in cases:
        result = await handle_notification_fn(
            case["notification"], llm_client=llm_client, submit_approval_fn=_null_submit_approval_fn
        )
        actual_behavior = "escalate" if result.get("status") == "escalated" else "draft"

        judge = None
        if actual_behavior == "draft":
            arguments = (result.get("approval_request") or {}).get("arguments", {})
            draft_text = arguments.get("reply_text") or arguments.get("message_text") or arguments.get("note") or ""
            if draft_text:
                judge = await judge_reply(case, draft_text, llm_client)
                judge_scores.append(judge)

        scored_cases.append({**case, "actual_behavior": actual_behavior, "judge": judge})

    escalation = escalation_precision(scored_cases)
    return {
        "case_count": len(cases),
        "escalation": escalation,
        "avg_reply_appropriateness": _avg(judge_scores, "reply_appropriateness"),
        "avg_reply_brand_voice_fidelity": _avg(judge_scores, "brand_voice_fidelity"),
        "per_case": scored_cases,
    }


async def run_all(
    llm_client: LLMClient,
    write_post_fn: WriteFn,
    handle_notification_fn: HandleNotificationFn,
) -> dict[str, t.Any]:
    posts = await run_post_evals(llm_client, write_post_fn)
    replies = await run_reply_evals(llm_client, handle_notification_fn)
    return {"posts": posts, "replies": replies}


# Metrics compared by compare_to_baseline() — only scalar, higher-is-better
# metrics belong here (a dropped score is a regression; escalation recall
# dropping is a regression too, escalation being over-triggered is not).
_REGRESSION_METRICS: list[tuple[str, ...]] = [
    ("posts", "must_avoid_pass_rate"),
    ("posts", "avg_brand_voice_fidelity"),
    ("posts", "avg_groundedness"),
    ("replies", "escalation", "recall"),
    ("replies", "escalation", "precision"),
]


def _get_path(d: dict[str, t.Any], path: tuple[str, ...]) -> float | None:
    value: t.Any = d
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value if isinstance(value, (int, float)) else None


def compare_to_baseline(
    current: dict[str, t.Any],
    baseline_path: Path = DEFAULT_BASELINE_PATH,
    threshold_pct: float = 5.0,
) -> None:
    """Raises RegressionError if any metric in _REGRESSION_METRICS drops more

    than `threshold_pct` percent versus the stored baseline (product spec: "No eval
    score may drop more than 5% between versions without explicit user
    sign-off"). If no baseline exists yet, this run establishes one instead
    of failing — there is nothing to regress against on the very first run.
    """
    if not baseline_path.exists():
        baseline_path.write_text(json.dumps(current, indent=2, default=str))
        return

    baseline = json.loads(baseline_path.read_text())

    regressions = []
    for path in _REGRESSION_METRICS:
        baseline_value = _get_path(baseline, path)
        current_value = _get_path(current, path)
        if baseline_value is None or current_value is None or baseline_value == 0:
            continue
        drop_pct = (baseline_value - current_value) / baseline_value * 100
        if drop_pct > threshold_pct:
            regressions.append(f"{'.'.join(path)} dropped {drop_pct:.1f}% (threshold: {threshold_pct}%)")

    if regressions:
        raise RegressionError(f"Eval regression: {regressions}")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(prog="python -m backend.evals.run_evals")
    parser.add_argument("--compare-to-baseline", action="store_true")
    args = parser.parse_args(argv)

    from app.agents.content_writer import write_post
    from app.agents.engagement import handle_notification

    try:
        from app.llmops.model_router import (
            route_and_call as llm_client,  # type: ignore[attr-defined]
        )
    except ImportError:
        print(
            "FAIL: no live LLM client wired up yet (app.llmops.model_router.route_and_call "
            "doesn't exist). The eval harness itself is ready — pytest backend/evals already "
            "exercises it against a fake client. Wire up a real client before running this CLI "
            "against production data.",
            file=sys.stderr,
        )
        return 1

    results = asyncio.run(run_all(llm_client, write_post, handle_notification))
    print(json.dumps(results, indent=2, default=str))

    if args.compare_to_baseline:
        try:
            compare_to_baseline(results)
        except RegressionError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        print("OK: no regression beyond threshold (or baseline established).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
