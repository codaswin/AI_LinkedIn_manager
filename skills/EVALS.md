# Evals Skill

Turn "feels like it works" into a tracked number. A golden dataset, programmatic metrics, LLM-as-judge, and a regression gate.

## Golden Dataset Format
```jsonl
{"id": "1", "input": "What's the eligibility for the CAT scholarship?", "expected_answer_contains": ["25%", "family income"], "expected_source": "scholarship_policy.pdf", "category": "factual"}
{"id": "2", "input": "Can you solve this integral for me: ...", "expected_behavior": "escalate", "category": "out_of_scope"}
```

## Metrics
```python
# evals/metrics.py
from dataclasses import dataclass


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    score: float
    details: str


def groundedness_score(response_text: str, cited_sources: list[str], expected_source: str) -> EvalResult:
    """Did the response actually cite the source it should have?"""
    passed = expected_source in cited_sources
    return EvalResult(case_id="", passed=passed, score=1.0 if passed else 0.0, details=f"cited={cited_sources}")


def escalation_precision(cases: list[dict]) -> dict:
    """Of cases that SHOULD escalate, how many did? Of cases that shouldn't, how many wrongly did?"""
    should_escalate = [c for c in cases if c["expected_behavior"] == "escalate"]
    correctly_escalated = sum(1 for c in should_escalate if c["actual_behavior"] == "escalate")
    should_not = [c for c in cases if c["expected_behavior"] != "escalate"]
    wrongly_escalated = sum(1 for c in should_not if c["actual_behavior"] == "escalate")

    recall = correctly_escalated / len(should_escalate) if should_escalate else 1.0
    precision = 1 - (wrongly_escalated / len(should_not)) if should_not else 1.0
    return {"recall": recall, "precision": precision}
```

## LLM-as-Judge (bias-aware)
```python
# evals/llm_judge.py
from app.llmops.model_router import route_and_call_sync

JUDGE_PROMPT = """You are evaluating an AI assistant's response for a mentorship platform.

Question: {question}
Reference answer (for context, not the only acceptable phrasing): {reference}
Assistant's response: {response}

Rate the response 1-5 on:
- Correctness: does it convey the right information?
- Groundedness: does it stick to what the source material supports, without adding unsupported claims?

Respond in JSON: {{"correctness": <1-5>, "groundedness": <1-5>, "reasoning": "<one sentence>"}}
"""


def judge_response(question: str, reference: str, response: str) -> dict:
    prompt = JUDGE_PROMPT.format(question=question, reference=reference, response=response)
    result = route_and_call_sync(task_type="evaluation", prompt=prompt)
    return parse_json(result)


def parse_json(text: str) -> dict:
    import json
    return json.loads(text)
```
**Bias note:** run the judge with response order randomized when comparing two candidate responses (position bias), and don't let response length alone drive the score — the prompt above scores on defined criteria, not "which sounds more thorough."

## Eval Runner + Regression Gate
```python
# evals/run_evals.py
import json
from app.evals.metrics import groundedness_score, escalation_precision
from app.evals.llm_judge import judge_response


def load_golden_set(path: str = "backend/evals/golden_set.jsonl") -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f]


def run_all(agent_fn) -> dict:
    cases = load_golden_set()
    results = []
    for case in cases:
        actual = agent_fn(case["input"])
        case["actual_behavior"] = actual.get("behavior")
        judge = judge_response(case["input"], case.get("expected_answer_contains", ""), actual["text"])
        results.append({"case_id": case["id"], "judge": judge, "category": case["category"]})

    escalation = escalation_precision(cases)
    avg_correctness = sum(r["judge"]["correctness"] for r in results) / len(results)

    return {"avg_correctness": avg_correctness, "escalation": escalation, "per_case": results}


def compare_to_baseline(current: dict, baseline_path: str = "backend/evals/baseline.json", threshold_pct: float = 5.0):
    with open(baseline_path) as f:
        baseline = json.load(f)

    regressions = []
    for key in ["avg_correctness"]:
        drop_pct = (baseline[key] - current[key]) / baseline[key] * 100
        if drop_pct > threshold_pct:
            regressions.append(f"{key} dropped {drop_pct:.1f}% (threshold: {threshold_pct}%)")

    if regressions:
        raise AssertionError(f"Eval regression: {regressions}")
```

## Best Practices
- Golden set needs enough examples per category to mean something — 3 examples isn't an eval suite, it's a smoke test
- Every eval run is stored (not just the pass/fail) so you can diff behavior across versions, not just scores
- The regression gate runs in CI on every change to prompts, tools, or retrieval — not just before major releases
- Update the golden set as real production failures are found — the eval suite should grow from actual usage, not stay frozen from day one
