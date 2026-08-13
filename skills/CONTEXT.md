# Context Skill

Deliberate context assembly with a hard token budget. This is where "the agent forgot X" bugs actually get fixed — by making inclusion/exclusion explicit and priority-ordered, not by hoping everything fits.

## Token Budgeting
```python
# context/budget.py
import tiktoken


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    enc = tiktoken.encoding_for_model(model)
    return len(enc.encode(text))


class TokenBudget:
    def __init__(self, model_context_window: int, reserved_for_output: int):
        self.total = model_context_window
        self.reserved_for_output = reserved_for_output
        self.available = model_context_window - reserved_for_output
        self.used = 0

    def can_fit(self, text: str) -> bool:
        return self.used + count_tokens(text) <= self.available

    def add(self, text: str) -> bool:
        tokens = count_tokens(text)
        if self.used + tokens > self.available:
            return False
        self.used += tokens
        return True

    def remaining(self) -> int:
        return self.available - self.used
```

## Assembler — priority-ordered, explicit about what's dropped
```python
# context/assembler.py
from app.context.budget import TokenBudget
from app.context.compaction import compact_conversation, select_top_chunks
from app.memory.semantic import read_semantic_memory
from app.memory.episodic import recent_episodes
from app.rag.retrieve import retrieve


async def assemble_context(state, runtime_agent_config: dict) -> dict:
    budget = TokenBudget(
        model_context_window=runtime_agent_config["context_window"],
        reserved_for_output=runtime_agent_config.get("reserved_for_output", 2000),
    )
    included, dropped = {}, []

    # Priority order — highest priority added first, never dropped once added
    for label, text in [
        ("system_prompt", runtime_agent_config["system_prompt"]),
        ("safety_rules", runtime_agent_config.get("safety_rules", "")),
        ("current_task", state.goal),
    ]:
        if budget.add(text):
            included[label] = text
        else:
            dropped.append(label)  # this should never happen — these are mandatory; escalate if it does

    # Lower priority — compact/select to fit remaining budget rather than dropping wholesale
    semantic = read_semantic_memory(state.scratchpad.get("entity_id", ""), state.goal, top_k=5, index_path="")
    fitted_semantic = select_top_chunks(semantic, budget)
    included["semantic_memory"] = fitted_semantic

    rag_chunks = retrieve(state.goal, top_k=8)
    fitted_rag = select_top_chunks(rag_chunks, budget)
    included["rag_context"] = fitted_rag
    if len(fitted_rag) < len(rag_chunks):
        dropped.append(f"rag_chunks: {len(rag_chunks) - len(fitted_rag)} dropped for budget")

    conversation = compact_conversation(state.conversation, budget)
    included["conversation"] = conversation

    return {"included": included, "dropped": dropped, "tokens_used": budget.used}
```

## Compaction — summarize, don't blindly truncate
```python
# context/compaction.py
from app.context.budget import TokenBudget, count_tokens
from app.llmops.model_router import route_and_call_sync


def select_top_chunks(chunks: list[dict], budget: TokenBudget) -> list[dict]:
    """Chunks are pre-sorted by relevance score — add highest-relevance first until budget runs out."""
    selected = []
    for chunk in chunks:
        text = chunk.get("text", chunk.get("fact", ""))
        if budget.add(text):
            selected.append(chunk)
        else:
            break
    return selected


def compact_conversation(conversation: list[dict], budget: TokenBudget) -> list[dict]:
    """Keep the most recent turns verbatim; summarize older turns into one entry rather than dropping them."""
    recent, older = conversation[-6:], conversation[:-6]
    result = []

    if older:
        summary_text = summarize_turns(older)
        if budget.add(summary_text):
            result.append({"role": "system", "content": f"[Earlier conversation summary]: {summary_text}"})

    for turn in recent:
        if budget.add(turn["content"]):
            result.append(turn)
        else:
            break  # even recent turns can be dropped if truly out of room — but this should be rare

    return result


def summarize_turns(turns: list[dict]) -> str:
    text = "\n".join(f"{t['role']}: {t['content']}" for t in turns)
    return route_and_call_sync(task_type="summarization", prompt=f"Summarize concisely:\n{text}")
```

## Best Practices
- Priority order lives in one place (`assembler.py`) — don't scatter "what matters most" logic across the codebase
- Log `dropped` on every call — if something mandatory gets dropped, that's a bug to fix, not a fact to accept
- Summarization is itself a traced, costed LLM call — it goes through `llmops`, same as anything else
- Re-evaluate the priority order using eval results, not intuition, once real usage data exists
