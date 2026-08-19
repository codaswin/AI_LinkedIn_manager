from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.llmops import anthropic_client
from app.llmops.anthropic_client import (
    AnthropicConfigError,
    call_anthropic,
    reset_client_cache,
)
from app.tenancy import context as tenancy_context
from app.tenancy import credentials as tenancy_credentials

_USER = "anthropic-test-user"


@pytest.fixture(autouse=True)
def _tenancy_context() -> None:
    tenancy_credentials.clear_user(_USER)
    token = tenancy_context.set_current_user_id(_USER)
    yield
    tenancy_context.reset_current_user_id(token)
    tenancy_credentials.clear_user(_USER)


@pytest.fixture(autouse=True)
def _clear_client_cache() -> None:
    reset_client_cache()
    yield
    reset_client_cache()


def _fake_response(payload: dict, input_tokens: int = 100, output_tokens: int = 50) -> SimpleNamespace:
    tool_use_block = SimpleNamespace(type="tool_use", input=payload)
    return SimpleNamespace(
        content=[tool_use_block],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


async def test_call_anthropic_extracts_structured_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=_fake_response(
                    {"text": "Hello, world!", "confidence": 0.9, "goal_achieved": True}
                )
            )
        )
    )
    monkeypatch.setattr(anthropic_client, "_get_client", lambda: fake_client)

    result = await call_anthropic(model="claude-sonnet-5", system_prompt="You are helpful.", user_content="Say hi")

    assert result.text == "Hello, world!"
    assert result.confidence == 0.9
    assert result.goal_achieved is True
    assert result.input_tokens == 100
    assert result.output_tokens == 50


async def test_call_anthropic_forces_the_response_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_response({"text": "x", "confidence": None, "goal_achieved": True})

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    monkeypatch.setattr(anthropic_client, "_get_client", lambda: fake_client)

    await call_anthropic(model="claude-sonnet-5", system_prompt="sys", user_content="user")

    assert captured["tool_choice"] == {"type": "tool", "name": "submit_agent_response"}
    assert captured["tools"][0]["name"] == "submit_agent_response"
    assert captured["system"] == "sys"
    assert captured["messages"] == [{"role": "user", "content": "user"}]


async def test_call_anthropic_confidence_can_be_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(return_value=_fake_response({"text": "no confidence here", "goal_achieved": True}))
        )
    )
    monkeypatch.setattr(anthropic_client, "_get_client", lambda: fake_client)

    result = await call_anthropic(model="claude-sonnet-5", system_prompt="sys", user_content="user")
    assert result.confidence is None


async def test_call_anthropic_raises_when_no_tool_use_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type="text", text="oops")], usage=SimpleNamespace(input_tokens=1, output_tokens=1)))
        )
    )
    monkeypatch.setattr(anthropic_client, "_get_client", lambda: fake_client)

    with pytest.raises(AnthropicConfigError, match="no tool_use block"):
        await call_anthropic(model="claude-sonnet-5", system_prompt="sys", user_content="user")


def test_missing_api_key_raises_config_error() -> None:
    with pytest.raises(AnthropicConfigError, match="ANTHROPIC_API_KEY"):
        anthropic_client._get_client()


def test_client_is_cached_across_calls() -> None:
    tenancy_credentials.set_credential(_USER, "ANTHROPIC_API_KEY", "test-key")
    first = anthropic_client._get_client()
    second = anthropic_client._get_client()
    assert first is second


def test_client_cache_is_isolated_per_user() -> None:
    other_user = "anthropic-other-user"
    tenancy_credentials.clear_user(other_user)
    tenancy_credentials.set_credential(_USER, "ANTHROPIC_API_KEY", "user-a-key")
    tenancy_credentials.set_credential(other_user, "ANTHROPIC_API_KEY", "user-b-key")

    mine = anthropic_client._get_client()

    token = tenancy_context.set_current_user_id(other_user)
    try:
        theirs = anthropic_client._get_client()
    finally:
        tenancy_context.reset_current_user_id(token)
        tenancy_credentials.clear_user(other_user)
        anthropic_client.reset_client_cache(other_user)

    assert mine is not theirs
