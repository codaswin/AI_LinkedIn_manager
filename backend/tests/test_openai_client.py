from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.llmops import openai_client
from app.llmops.openai_client import (
    OpenAIConfigError,
    call_openai,
    reset_client_cache,
)


@pytest.fixture(autouse=True)
def _clear_client_cache() -> None:
    reset_client_cache()
    yield
    reset_client_cache()


def _fake_response(payload: dict, input_tokens: int = 100, output_tokens: int = 50) -> SimpleNamespace:
    tool_call = SimpleNamespace(
        function=SimpleNamespace(name="submit_agent_response", arguments=json.dumps(payload))
    )
    message = SimpleNamespace(tool_calls=[tool_call])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens),
    )


async def test_call_openai_extracts_structured_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=_fake_response(
                        {"text": "Hello, world!", "confidence": 0.9, "goal_achieved": True}
                    )
                )
            )
        )
    )
    monkeypatch.setattr(openai_client, "_get_client", lambda: fake_client)

    result = await call_openai(model="gpt-4o-mini", system_prompt="You are helpful.", user_content="Say hi")

    assert result.text == "Hello, world!"
    assert result.confidence == 0.9
    assert result.goal_achieved is True
    assert result.input_tokens == 100
    assert result.output_tokens == 50


async def test_call_openai_forces_the_response_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_response({"text": "x", "confidence": None, "goal_achieved": True})

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(openai_client, "_get_client", lambda: fake_client)

    await call_openai(model="gpt-4o-mini", system_prompt="sys", user_content="user")

    assert captured["tool_choice"] == {"type": "function", "function": {"name": "submit_agent_response"}}
    assert captured["tools"][0]["function"]["name"] == "submit_agent_response"
    assert captured["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]


async def test_call_openai_confidence_can_be_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_fake_response({"text": "no confidence here", "goal_achieved": True}))
            )
        )
    )
    monkeypatch.setattr(openai_client, "_get_client", lambda: fake_client)

    result = await call_openai(model="gpt-4o-mini", system_prompt="sys", user_content="user")
    assert result.confidence is None


async def test_call_openai_raises_when_no_matching_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=None))],
                        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
                    )
                )
            )
        )
    )
    monkeypatch.setattr(openai_client, "_get_client", lambda: fake_client)

    with pytest.raises(OpenAIConfigError, match="no submit_agent_response tool call"):
        await call_openai(model="gpt-4o-mini", system_prompt="sys", user_content="user")


def test_missing_api_key_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(OpenAIConfigError, match="OPENAI_API_KEY"):
        openai_client._get_client()


def test_client_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    first = openai_client._get_client()
    second = openai_client._get_client()
    assert first is second
