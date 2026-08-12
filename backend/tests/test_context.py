from __future__ import annotations

import pytest

from app.context.assembler import PRIORITY_ORDER, assemble_context
from app.context.budget import TIER_BUDGETS, count_tokens, get_budget, register_tier_budget


def _blocks_by_label(blocks: list[dict]) -> dict[str, dict]:
    return {block["label"]: block for block in blocks}


def test_everything_fits_appears_in_priority_order() -> None:
    components = {
        "system_prompt": "You are the Content Writer agent.",
        "safety_rules": "Never post without approval.",
        "current_task": "Draft a post about agentic RAG.",
        "semantic_memory": "Brand voice: concise, technical, no hype.",
        "rag_context": "Recent article: vLLM serving improvements.",
        "episodic_memory": "Last post on this topic was 3 weeks ago.",
        "older_conversation": "User: keep it under 200 words.",
    }
    budget = 5000

    blocks = assemble_context(components, budget)

    assert [b["label"] for b in blocks] == list(PRIORITY_ORDER)
    for label, text in components.items():
        block = _blocks_by_label(blocks)[label]
        assert block["content"] == text
        assert block["compacted"] is False


def test_over_budget_compacts_lowest_priority_first() -> None:
    components = {
        "system_prompt": "System prompt.",
        "safety_rules": "Safety rules.",
        "current_task": "Current task.",
        "semantic_memory": "s" * 400,
        "rag_context": "r" * 400,
        "episodic_memory": "e" * 400,
        "older_conversation": "o" * 400,
    }
    budget = 260

    blocks = assemble_context(components, budget)
    by_label = _blocks_by_label(blocks)

    assert by_label["older_conversation"]["compacted"] is True
    assert by_label["older_conversation"]["content"] != components["older_conversation"]
    assert "[compacted" in by_label["older_conversation"]["content"]

    assert by_label["episodic_memory"]["compacted"] is True
    assert by_label["episodic_memory"]["content"] != components["episodic_memory"]
    assert "[compacted" in by_label["episodic_memory"]["content"]

    assert by_label["rag_context"]["compacted"] is False
    assert by_label["rag_context"]["content"] == components["rag_context"]

    assert by_label["semantic_memory"]["compacted"] is False
    assert by_label["semantic_memory"]["content"] == components["semantic_memory"]

    all_labels = [b["label"] for b in blocks]
    assert all_labels.index("older_conversation") > all_labels.index("episodic_memory")


def test_system_prompt_and_safety_rules_never_compact_or_drop() -> None:
    system_prompt = "S" * 2000
    safety_rules = "R" * 2000
    components = {
        "system_prompt": system_prompt,
        "safety_rules": safety_rules,
        "current_task": "T" * 2000,
        "semantic_memory": "M" * 2000,
        "rag_context": "G" * 2000,
        "episodic_memory": "E" * 2000,
        "older_conversation": "O" * 2000,
    }
    tiny_budget = 10

    blocks = assemble_context(components, tiny_budget)
    by_label = _blocks_by_label(blocks)

    assert by_label["system_prompt"]["content"] == system_prompt
    assert by_label["system_prompt"]["compacted"] is False
    assert by_label["safety_rules"]["content"] == safety_rules
    assert by_label["safety_rules"]["compacted"] is False

    assert "system_prompt" in by_label
    assert "safety_rules" in by_label


def test_different_tier_budgets_produce_different_assembly() -> None:
    TIER_BUDGETS.clear()
    register_tier_budget("primary", 100_000)
    register_tier_budget("worker", 300)

    oversized_conversation = "turn " * 2000
    components = {
        "system_prompt": "System prompt.",
        "safety_rules": "Safety rules.",
        "current_task": "Current task.",
        "semantic_memory": "Semantic memory facts.",
        "rag_context": "RAG chunk.",
        "episodic_memory": "Episodic memory summary.",
        "older_conversation": oversized_conversation,
    }

    primary_blocks = _blocks_by_label(assemble_context(components, get_budget("primary")))
    worker_blocks = _blocks_by_label(assemble_context(components, get_budget("worker")))

    assert primary_blocks["older_conversation"]["compacted"] is False
    assert primary_blocks["older_conversation"]["content"] == oversized_conversation

    assert worker_blocks["older_conversation"]["compacted"] is True
    assert worker_blocks["older_conversation"]["content"] != oversized_conversation

    assert count_tokens(worker_blocks["older_conversation"]["content"]) < count_tokens(
        primary_blocks["older_conversation"]["content"]
    )


def test_get_budget_raises_for_unregistered_tier() -> None:
    TIER_BUDGETS.clear()
    with pytest.raises(KeyError):
        get_budget("nonexistent_tier")
