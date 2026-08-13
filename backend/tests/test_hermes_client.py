from __future__ import annotations

import json

import pytest
from app.llmops import hermes_client
from app.llmops.hermes_client import HermesCallError, call_hermes


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _tool_call_payload(arguments: dict, prompt_tokens: int = 80, completion_tokens: int = 40) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"name": "submit_agent_response", "arguments": json.dumps(arguments)}}
                    ]
                }
            }
        ],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


async def test_call_hermes_extracts_structured_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request_with_retry(client, method, url, **kwargs):
        assert method == "POST"
        assert url == "http://localhost:8001/v1/chat/completions"
        return _FakeResponse(_tool_call_payload({"text": "triage result", "confidence": 0.6, "goal_achieved": True}))

    monkeypatch.setattr(hermes_client, "request_with_retry", fake_request_with_retry)

    result = await call_hermes(
        endpoint="http://localhost:8001/v1", model="hermes-3", system_prompt="sys", user_content="user"
    )

    assert result.text == "triage result"
    assert result.confidence == 0.6
    assert result.goal_achieved is True
    assert result.input_tokens == 80
    assert result.output_tokens == 40


async def test_call_hermes_forces_the_response_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def fake_request_with_retry(client, method, url, **kwargs):
        captured.update(kwargs)
        return _FakeResponse(_tool_call_payload({"text": "x", "goal_achieved": True}))

    monkeypatch.setattr(hermes_client, "request_with_retry", fake_request_with_retry)

    await call_hermes(endpoint="http://localhost:8001/v1", model="hermes-3", system_prompt="sys", user_content="user")

    body = captured["json"]
    assert body["tool_choice"] == {"type": "function", "function": {"name": "submit_agent_response"}}
    assert body["tools"][0]["function"]["name"] == "submit_agent_response"
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}]


async def test_call_hermes_strips_trailing_slash_from_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = []

    async def fake_request_with_retry(client, method, url, **kwargs):
        urls.append(url)
        return _FakeResponse(_tool_call_payload({"text": "x", "goal_achieved": True}))

    monkeypatch.setattr(hermes_client, "request_with_retry", fake_request_with_retry)

    await call_hermes(endpoint="http://localhost:8001/v1/", model="hermes-3", system_prompt="sys", user_content="user")
    assert urls == ["http://localhost:8001/v1/chat/completions"]


async def test_call_hermes_raises_when_no_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request_with_retry(client, method, url, **kwargs):
        return _FakeResponse({"choices": [{"message": {"content": "plain text, no tool call"}}]})

    monkeypatch.setattr(hermes_client, "request_with_retry", fake_request_with_retry)

    with pytest.raises(HermesCallError, match="no usable tool call|no tool call"):
        await call_hermes(endpoint="http://localhost:8001/v1", model="hermes-3", system_prompt="sys", user_content="user")


async def test_call_hermes_confidence_can_be_null(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request_with_retry(client, method, url, **kwargs):
        return _FakeResponse(_tool_call_payload({"text": "no confidence field", "goal_achieved": False}))

    monkeypatch.setattr(hermes_client, "request_with_retry", fake_request_with_retry)

    result = await call_hermes(endpoint="http://localhost:8001/v1", model="hermes-3", system_prompt="sys", user_content="user")
    assert result.confidence is None
    assert result.goal_achieved is False


async def test_call_hermes_missing_usage_defaults_to_zero_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request_with_retry(client, method, url, **kwargs):
        payload = _tool_call_payload({"text": "x", "goal_achieved": True})
        del payload["usage"]
        return _FakeResponse(payload)

    monkeypatch.setattr(hermes_client, "request_with_retry", fake_request_with_retry)

    result = await call_hermes(endpoint="http://localhost:8001/v1", model="hermes-3", system_prompt="sys", user_content="user")
    assert result.input_tokens == 0
    assert result.output_tokens == 0
