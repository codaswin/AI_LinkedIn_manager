"""Thin wrapper around the Anthropic SDK for the primary/cheap model tiers.

Isolated in its own module (not inlined into model_router.py) so
model_router.route_and_call() stays provider-agnostic and mocking the
actual API call in tests is one clean seam (`anthropic_client.call_anthropic`),
matching how every other external integration in this codebase (Composio,
the research-source tools) keeps its raw SDK/HTTP call in one place.

Structured-output trick: rather than asking the model for free text and
separately asking it to self-report a confidence score (two calls, two
round trips), every call here forces exactly one tool call
(`RESPONSE_TOOL_NAME`) whose `text` field is defined as "your literal answer
to the task, verbatim" — so `response.text` downstream is EXACTLY what every
existing agent's own `_parse_*()` function already expects (a PostBrief JSON
string, raw post prose, a triage index array, ...), while `confidence` and
`goal_achieved` ride along as separate fields on that same call. No agent's
parsing contract changes because of this.
"""

from __future__ import annotations

from dataclasses import dataclass

import anthropic
from app.tenancy.context import get_current_user_id
from app.tenancy.credentials import resolve_credential

RESPONSE_TOOL_NAME = "submit_agent_response"

RESPONSE_TOOL_SCHEMA: dict = {
    "name": RESPONSE_TOOL_NAME,
    "description": "Submit your response to the current task. Call this exactly once, with nothing else.",
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "Your complete answer to the task, in EXACTLY the format the task "
                    "instructions specify (plain prose, or a raw JSON string if the task "
                    "asks for JSON). This is passed on verbatim to whatever parses it next "
                    "— do not add commentary outside it."
                ),
            },
            "confidence": {
                "type": ["number", "null"],
                "description": "0.0-1.0 confidence this response is accurate/ready-to-use as-is. Use null if the task doesn't call for a confidence score.",
            },
            "goal_achieved": {
                "type": "boolean",
                "description": "True if this response fully completes the current task and no further iteration is needed.",
            },
        },
        "required": ["text", "goal_achieved"],
    },
}

_DEFAULT_MAX_TOKENS = 4096

_clients: dict[str, anthropic.AsyncAnthropic] = {}


class AnthropicConfigError(RuntimeError):
    """Raised when ANTHROPIC_API_KEY is missing or the API returns an unusable response."""


def _get_client() -> anthropic.AsyncAnthropic:
    user_id = get_current_user_id()
    client = _clients.get(user_id)
    if client is not None:
        return client
    api_key = resolve_credential("ANTHROPIC_API_KEY")
    if not api_key:
        raise AnthropicConfigError("ANTHROPIC_API_KEY is not set. Set it on the Connections page before using route_and_call.")
    client = anthropic.AsyncAnthropic(api_key=api_key)
    _clients[user_id] = client
    return client


def reset_client_cache(user_id: str | None = None) -> None:
    """Forces the client to be re-resolved (and re-check the credential) on next call.

    Called automatically whenever a user saves/deletes their Anthropic key
    (see app.memory.platform_credentials) — pass no user_id to clear every
    cached client at once (used by test fixtures).
    """
    if user_id is None:
        _clients.clear()
    else:
        _clients.pop(user_id, None)


@dataclass
class AnthropicCallResult:
    text: str
    confidence: float | None
    goal_achieved: bool
    input_tokens: int
    output_tokens: int


async def call_anthropic(
    *,
    model: str,
    system_prompt: str,
    user_content: str,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> AnthropicCallResult:
    client = _get_client()
    # Plain dict literals are runtime-compatible with the SDK's TypedDict
    # params (that's what a TypedDict is), but mypy's overload resolution
    # doesn't structurally match a bare `dict` against them — hence the
    # ignore rather than importing every one of the SDK's param TypedDicts
    # just to satisfy the type checker for a call that already works.
    response = await client.messages.create(  # type: ignore[call-overload]
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
        tools=[RESPONSE_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": RESPONSE_TOOL_NAME},
    )

    tool_use = next((block for block in response.content if getattr(block, "type", None) == "tool_use"), None)
    if tool_use is None:
        raise AnthropicConfigError(f"Anthropic response had no tool_use block: {response.content!r}")

    payload = tool_use.input
    return AnthropicCallResult(
        text=str(payload.get("text", "")),
        confidence=payload.get("confidence"),
        goal_achieved=bool(payload.get("goal_achieved", False)),
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
