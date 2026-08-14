"""Safety audit CLI — run as `python -m app.safety.audit` or
`python -m backend.app.safety.audit` (the latter is what the PRP's
validation gates invoke from the repo root). Exits non-zero with a clear
message on any failure; the exact phrase "zero ungated risky tools" is
printed on success for the validation gate to grep.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `app.*` importable regardless of invocation style. `python -m
# app.safety.audit` run with cwd=backend/ already has `backend/` on
# sys.path[0]; `python -m backend.app.safety.audit` run from the repo root
# does not (only `backend` itself resolves, not the `app` package nested
# inside it), so this file's own `from app...` imports below would otherwise
# fail with ModuleNotFoundError under that invocation style. Must run before
# any `app.` import.
_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.llmops import cost_tracker
from app.safety import guardrails
from app.tools.registry import _import_all_tools, registry

_REFUSAL_TOPIC_COUNT = 5

# Files exempt from the ungated-`approved=True` scan: approval_gate.py is the
# one place that literal is legitimately allowed to appear (it's the sole
# caller of execute_tool(..., approved=True)); audit.py (this file) contains
# the literal itself only as the string being searched for, which would
# otherwise flag itself as a false positive.
_SCAN_EXEMPT_FILENAMES = {"approval_gate.py", "audit.py"}
_APPROVED_TRUE_LITERAL = "approved=True"


def scan_ungated_approved_true(
    agents_dir: Path | None = None, safety_dir: Path | None = None
) -> list[str]:
    """Static grep-style scan: `approved=True` must appear only inside approval_gate.py.

    Mirrors the technique llmops-agent uses to prove no hardcoded model
    names leak outside model_router.py — a plain literal-string scan across
    the directories where a runtime agent or safety module could plausibly
    bypass the approval gate.
    """
    safety_dir = safety_dir or Path(__file__).resolve().parent
    agents_dir = agents_dir or (safety_dir.parent / "agents")

    violations: list[str] = []
    targets = sorted(agents_dir.glob("*.py")) + sorted(safety_dir.glob("*.py"))
    for path in targets:
        if path.name in _SCAN_EXEMPT_FILENAMES:
            continue
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _APPROVED_TRUE_LITERAL in line:
                violations.append(f"{path}:{lineno}: {line.strip()}")
    return violations


def check_no_ungated_risky_tools() -> list[str]:
    _import_all_tools()
    errors: list[str] = []
    for name, reg in registry.all().items():
        if not isinstance(reg.definition.requires_approval, bool):
            errors.append(f"tool '{name}': requires_approval is not an explicit bool")
    errors.extend(scan_ungated_approved_true())
    return errors


def check_confidence_threshold_and_refusal_topics() -> list[str]:
    errors: list[str] = []
    if guardrails.CONFIDENCE_THRESHOLD != 0.75:
        errors.append(
            f"guardrails.CONFIDENCE_THRESHOLD is {guardrails.CONFIDENCE_THRESHOLD!r}, expected 0.75"
        )

    topics = guardrails.REFUSAL_TOPICS
    if len(topics) < _REFUSAL_TOPIC_COUNT:
        errors.append(
            f"only {len(topics)} refusal topic(s) registered in guardrails.REFUSAL_TOPICS, "
            f"expected at least {_REFUSAL_TOPIC_COUNT}"
        )
    for topic, patterns in topics.items():
        if not patterns:
            errors.append(f"refusal topic '{topic}' has no patterns registered")
    return errors


def check_cost_cap_hard_stop() -> list[str]:
    """Lightweight smoke check that check_budget() raises once over budget.

    Does not duplicate llmops' own test suite — just proves, in this
    process, that going over budget raises rather than merely logging.
    Manipulates and then resets the audit-specific Redis cost key; safe because
    audit.py runs as a short-lived standalone process.
    """
    errors: list[str] = []
    budget = cost_tracker.get_cost_summary()["budget_usd"]
    cost_tracker.record_cost(budget + 1_000_000.0)
    try:
        cost_tracker.check_budget()
        errors.append(
            "cost_tracker.check_budget() did not raise once spend exceeded the daily budget "
            "— the cost cap must hard-stop, not just log"
        )
    except cost_tracker.CostBudgetExceededError:
        pass
    finally:
        cost_tracker.reset_for_testing()
    return errors


def run_audit() -> tuple[bool, list[str]]:
    errors: list[str] = []
    errors.extend(check_no_ungated_risky_tools())
    errors.extend(check_confidence_threshold_and_refusal_topics())
    errors.extend(check_cost_cap_hard_stop())
    return (len(errors) == 0, errors)


def main(argv: list[str] | None = None) -> int:
    ok, errors = run_audit()
    if not ok:
        print(f"FAIL: {len(errors)} safety audit finding(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(
        "PASS: zero ungated risky tools; confidence threshold and refusal-topic "
        "guardrails intact; cost cap hard-stops."
    )
    return 0


if __name__ == "__main__":
    # Same double-import guard as tools/registry.py's __main__ block: force a
    # single canonical import of this module so any state it touches isn't
    # split across two module identities (__main__ vs app.safety.audit).
    from app.safety.audit import main as _canonical_main

    sys.exit(_canonical_main())
