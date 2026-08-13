"""Registry mapping each runtime agent's system prompt to a name.

Later phases (the Phase 2 backend builds for the 5 runtime agents) register
their real system prompts here via register_prompt(); nothing downstream
should ever hardcode a prompt string inline — it should call get_prompt()
instead, so prompt changes stay reviewable, versioned artifacts rather than
in-place string edits (skills/LLMOPS.md § Best Practices).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class PromptVersion:
    agent_name: str
    version: int
    text: str
    registered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


_registry: dict[str, list[PromptVersion]] = {}


def register_prompt(agent_name: str, prompt_text: str) -> PromptVersion:
    """Register a new version of an agent's system prompt.

    Each call appends a new immutable version rather than editing one in
    place, so a regression can always be traced back to the version that
    introduced it and rolled back without guessing at the prior text.
    """
    if not agent_name:
        raise ValueError("agent_name must be non-empty")
    versions = _registry.setdefault(agent_name, [])
    version = PromptVersion(agent_name=agent_name, version=len(versions) + 1, text=prompt_text)
    versions.append(version)
    return version


def get_prompt(agent_name: str) -> str:
    """Return the most recently registered prompt text for an agent.

    Raises KeyError if nothing has been registered yet — callers must not
    fall back to an inline string, since that is exactly the hardcoding this
    registry exists to prevent.
    """
    versions = _registry.get(agent_name)
    if not versions:
        raise KeyError(f"No prompt registered for agent {agent_name!r}. Call register_prompt() first.")
    return versions[-1].text


def get_prompt_history(agent_name: str) -> list[PromptVersion]:
    """All registered versions for an agent, oldest first."""
    return list(_registry.get(agent_name, []))


def reset_for_testing() -> None:
    """Clear the registry. Test-only."""
    _registry.clear()


def snapshot_for_testing() -> dict[str, list[PromptVersion]]:
    """Shallow-copy current registry state. Test-only, pairs with restore_for_testing().

    Every runtime agent module registers its system prompt exactly once, at
    import time. A test that clears the registry for isolation and then
    leaves it empty permanently starves every later test (in any file) that
    relies on that one-time registration having survived — module imports
    are cached, so it never runs again. Snapshot before clearing, restore
    after, so isolation doesn't outlive the test that needed it.
    """
    return {agent: list(versions) for agent, versions in _registry.items()}


def restore_for_testing(snapshot: dict[str, list[PromptVersion]]) -> None:
    """Replace the registry with a previously captured snapshot. Test-only."""
    _registry.clear()
    _registry.update(snapshot)
