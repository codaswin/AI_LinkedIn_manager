"""Thin wrapper around the OpenAI SDK for the primary/cheap model tiers.

Mirrors anthropic_client.py's shape exactly (same AnthropicCallResult-style
dataclass fields, same "force one tool call" structured-output trick) so
model_router.route_and_call() can treat the two providers interchangeably —
see that module's provider branch. Isolated in its own module for the same
reason anthropic_client.py is: one clean seam (`openai_client.call_openai`)
to mock in tests, matching how every other external integration in this
codebase keeps its raw SDK/HTTP call in one place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import openai
from app.tenancy.context import get_current_user_id
from app.tenancy.credentials import resolve_credential

RESPONSE_TOOL_NAME = "submit_agent_response"

RESPONSE_TOOL_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": RESPONSE_TOOL_NAME,
        "description": "Submit your response to the current task. Call this exactly once, with nothing else.",
        # strict=True switches OpenAI from best-effort JSON generation to
        # grammar-constrained decoding: the API guarantees `arguments` is
        # always syntactically valid JSON matching this schema, token by
        # token. Without it, a task that asks the model to nest a whole
        # JSON object inside the "text" string (every agent that wants a
        # structured reply, e.g. content_strategist's PostBrief) requires
        # the model to freehand double-escape quotes — one dropped
        # backslash desyncs the outer JSON, and the smaller/cheaper models
        # this tier uses have been observed spiraling into runaway
        # newline-padded repetition trying to recover, burning the whole
        # max_completion_tokens budget on a single malformed call (see the
        # "OpenAI tool call arguments were not valid JSON" production
        # incident this schema addition fixes). strict mode requires every
        # property listed in "required" (nullable fields stay optional via
        # a `["type", "null"]` union, as confidence already does) and
        # "additionalProperties": False on every object level.
        "strict": True,
        "parameters": {
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
            "required": ["text", "confidence", "goal_achieved"],
            "additionalProperties": False,
        },
    },
}

_DEFAULT_MAX_TOKENS = 4096

_clients: dict[str, openai.AsyncOpenAI] = {}


class OpenAIConfigError(RuntimeError):
    """Raised when OPENAI_API_KEY is missing or the API returns an unusable response."""


def _get_client() -> openai.AsyncOpenAI:
    user_id = get_current_user_id()
    client = _clients.get(user_id)
    if client is not None:
        return client
    api_key = resolve_credential("OPENAI_API_KEY")
    if not api_key:
        raise OpenAIConfigError("OPENAI_API_KEY is not set. Set it on the Connections page before using route_and_call.")
    client = openai.AsyncOpenAI(api_key=api_key)
    _clients[user_id] = client
    return client


def reset_client_cache(user_id: str | None = None) -> None:
    """Forces the client to be re-resolved (and re-check the credential) on next call.

    Called automatically whenever a user saves/deletes their OpenAI key (see
    app.memory.platform_credentials) — pass no user_id to clear every
    cached client at once (used by test fixtures).
    """
    if user_id is None:
        _clients.clear()
    else:
        _clients.pop(user_id, None)


@dataclass
class OpenAICallResult:
    text: str
    confidence: float | None
    goal_achieved: bool
    input_tokens: int
    output_tokens: int


async def call_openai(
    *,
    model: str,
    system_prompt: str,
    user_content: str,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> OpenAICallResult:
    client = _get_client()
    # Plain dict literals are runtime-compatible with the SDK's TypedDict
    # params (that's what a TypedDict is), but mypy's overload resolution
    # doesn't structurally match a bare `dict` against them — hence the
    # ignore rather than importing every one of the SDK's param TypedDicts
    # just to satisfy the type checker for a call that already works (same
    # tradeoff anthropic_client.py's call_anthropic makes).
    response = await client.chat.completions.create(  # type: ignore[call-overload]
        model=model,
        max_completion_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        tools=[RESPONSE_TOOL_SCHEMA],
        tool_choice={"type": "function", "function": {"name": RESPONSE_TOOL_NAME}},
    )

    message = response.choices[0].message
    tool_calls = message.tool_calls or []
    tool_call = next((tc for tc in tool_calls if tc.function.name == RESPONSE_TOOL_NAME), None)
    if tool_call is None:
        raise OpenAIConfigError(f"OpenAI response had no {RESPONSE_TOOL_NAME} tool call: {message!r}")

    try:
        payload = json.loads(tool_call.function.arguments)
    except json.JSONDecodeError as exc:
        raise OpenAIConfigError(f"OpenAI tool call arguments were not valid JSON: {tool_call.function.arguments!r}") from exc

    usage = response.usage
    return OpenAICallResult(
        text=str(payload.get("text", "")),
        confidence=payload.get("confidence"),
        goal_achieved=bool(payload.get("goal_achieved", False)),
        input_tokens=usage.prompt_tokens if usage else 0,
        output_tokens=usage.completion_tokens if usage else 0,
    )
