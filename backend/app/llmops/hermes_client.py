"""Thin wrapper around a Hermes/vLLM OpenAI-compatible chat-completions

endpoint for the worker model tier. Uses the same forced-tool-call
structured-output trick as anthropic_client.py (see that module's
docstring) — models in the Hermes family are specifically known for strong
function-calling support, and vLLM's OpenAI-compatible server exposes
`tools`/`tool_choice` for compatible models the same way OpenAI's API does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from app.llmops.anthropic_client import RESPONSE_TOOL_NAME, RESPONSE_TOOL_SCHEMA
from app.tools.http_utils import request_with_retry

_DEFAULT_MAX_TOKENS = 2048

_OPENAI_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": RESPONSE_TOOL_SCHEMA["name"],
        "description": RESPONSE_TOOL_SCHEMA["description"],
        "parameters": RESPONSE_TOOL_SCHEMA["input_schema"],
    },
}


class HermesCallError(RuntimeError):
    """Raised when the Hermes/vLLM endpoint returns a response with no usable tool call."""


@dataclass
class HermesCallResult:
    text: str
    confidence: float | None
    goal_achieved: bool
    input_tokens: int
    output_tokens: int


async def call_hermes(
    *,
    endpoint: str,
    model: str,
    system_prompt: str,
    user_content: str,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> HermesCallResult:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await request_with_retry(
            client,
            "POST",
            f"{endpoint.rstrip('/')}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "tools": [_OPENAI_TOOL_SCHEMA],
                "tool_choice": {"type": "function", "function": {"name": RESPONSE_TOOL_NAME}},
                "max_tokens": max_tokens,
            },
        )
    payload = response.json()
    choice = (payload.get("choices") or [{}])[0]
    tool_calls = (choice.get("message") or {}).get("tool_calls") or []
    if not tool_calls:
        raise HermesCallError(f"Hermes/vLLM response had no tool call: {payload!r}")

    arguments = json.loads(tool_calls[0]["function"]["arguments"])
    usage = payload.get("usage") or {}
    return HermesCallResult(
        text=str(arguments.get("text", "")),
        confidence=arguments.get("confidence"),
        goal_achieved=bool(arguments.get("goal_achieved", False)),
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
    )
