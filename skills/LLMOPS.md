# LLM Ops Skill

Model routing, tracing, cost tracking, prompt versioning — the operational backbone. No LLM call in the system bypasses this layer.

## Model Router
```python
# llmops/model_router.py
from openai import OpenAI, AsyncOpenAI
from anthropic import Anthropic
import httpx
from app.config import settings
from app.llmops.tracer import trace_llm_call
from app.llmops.cost_tracker import record_cost
from app.safety.cost_cap import is_over_budget

ROUTING_TABLE = {
    "routing": {"provider": "openai", "model": "gpt-4o-mini"},           # cheap, fast — classify/route only
    "generation": {"provider": "anthropic", "model": "claude-sonnet"},    # the main reasoning/response model
    "summarization": {"provider": "openai", "model": "gpt-4o-mini"},
    "evaluation": {"provider": "anthropic", "model": "claude-sonnet"},
    "high_volume_worker": {"provider": "hermes", "model": "hermes-3"},   # self-hosted, for cheap high-volume tasks
}

_openai = AsyncOpenAI()
_anthropic = Anthropic()


async def route_and_call(task_type: str, context: dict, state) -> "LLMResponse":
    if is_over_budget():
        raise RuntimeError("Daily cost budget exceeded — halting before making the call")

    route = ROUTING_TABLE[task_type]
    prompt = render_prompt(context)

    if route["provider"] == "openai":
        resp = await _openai.chat.completions.create(model=route["model"], messages=prompt)
        text, tokens_in, tokens_out = resp.choices[0].message.content, resp.usage.prompt_tokens, resp.usage.completion_tokens
    elif route["provider"] == "anthropic":
        resp = _anthropic.messages.create(model=route["model"], messages=prompt, max_tokens=2000)
        text, tokens_in, tokens_out = resp.content[0].text, resp.usage.input_tokens, resp.usage.output_tokens
    elif route["provider"] == "hermes":
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{settings.HERMES_ENDPOINT}/chat/completions", json={"model": route["model"], "messages": prompt})
            data = resp.json()
            text, tokens_in, tokens_out = data["choices"][0]["message"]["content"], data["usage"]["prompt_tokens"], data["usage"]["completion_tokens"]

    cost = estimate_cost(route["model"], tokens_in, tokens_out)
    record_cost(cost)
    trace_llm_call(state.task_id if state else "n/a", route["model"], tokens_in, tokens_out, cost)

    return LLMResponse(text=text, cost_usd=cost, tool_calls=[], goal_achieved=False, confidence=1.0)


def route_and_call_sync(task_type: str, prompt: str) -> str:
    """Sync wrapper for non-loop contexts like evals and summarization."""
    ...


class LLMResponse:
    def __init__(self, text, cost_usd, tool_calls, goal_achieved, confidence):
        self.text, self.cost_usd, self.tool_calls = text, cost_usd, tool_calls
        self.goal_achieved, self.confidence = goal_achieved, confidence


def render_prompt(context: dict) -> list[dict]:
    ...


PRICING_PER_1K = {"gpt-4o-mini": 0.00015, "claude-sonnet": 0.003, "hermes-3": 0.0}  # self-hosted = infra cost only


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    rate = PRICING_PER_1K.get(model, 0.003)
    return ((tokens_in + tokens_out) / 1000) * rate
```

## Tracer
```python
# llmops/tracer.py
import structlog
import uuid

logger = structlog.get_logger()


def trace_llm_call(task_id: str, model: str, tokens_in: int, tokens_out: int, cost_usd: float):
    logger.info("llm_call", task_id=task_id, model=model, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd)


def trace_step(task_id: str, step_type: str, data):
    logger.info("agent_step", task_id=task_id, step_type=step_type, trace_id=str(uuid.uuid4()))
```
For hosted tracing (Langfuse/Phoenix), swap the `logger.info` calls for the relevant SDK call — keep the function signatures the same so nothing else in the codebase changes.

## Cost Tracker
```python
# llmops/cost_tracker.py
from app.safety.cost_cap import add_spend, get_today_spend

def record_cost(amount_usd: float):
    add_spend(amount_usd)

def get_cost_summary() -> dict:
    return {"today_usd": get_today_spend()}
```

## Prompt Registry (versioned, not in-place edits)
```python
# llmops/prompt_registry.py
from dataclasses import dataclass
from datetime import datetime


@dataclass
class PromptVersion:
    agent_name: str
    version: int
    text: str
    created_at: datetime
    approved_by: str | None = None


_registry: dict[str, list[PromptVersion]] = {}


def register_prompt(agent_name: str, text: str, approved_by: str | None = None) -> PromptVersion:
    versions = _registry.setdefault(agent_name, [])
    version = PromptVersion(agent_name=agent_name, version=len(versions) + 1, text=text, created_at=datetime.utcnow(), approved_by=approved_by)
    versions.append(version)
    return version


def get_active_prompt(agent_name: str) -> str:
    versions = _registry.get(agent_name, [])
    approved = [v for v in versions if v.approved_by]
    return approved[-1].text if approved else versions[-1].text
```

## Best Practices
- Every LLM call — including from evals and the reflection job — goes through `route_and_call`, no exceptions, or cost tracking and tracing silently miss calls
- Route cheap/high-volume tasks (routing, classification) to a small or self-hosted model; reserve the strong model for actual reasoning/generation
- Prompt changes are new versions, never in-place string edits — this is what makes `learning-agent`'s proposals reviewable and rollback-able
- Cost tracking checks the budget before the call, not after — see `safety/cost_cap.py`
