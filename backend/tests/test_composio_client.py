from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.tools import composio_client
from app.tools.composio_client import (
    ComposioConfigError,
    execute_linkedin_action,
    execute_x_action,
    get_composio_entity_id,
    get_linkedin_author_urn,
    get_linkedin_connected_account_id,
    get_x_connected_account_id,
    reset_client_cache,
)


@pytest.fixture(autouse=True)
def _clear_client_cache() -> None:
    reset_client_cache()
    yield
    reset_client_cache()


def test_missing_api_key_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    with pytest.raises(ComposioConfigError, match="COMPOSIO_API_KEY"):
        composio_client.get_composio_client()


def test_missing_linkedin_connected_account_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPOSIO_LINKEDIN_CONNECTED_ACCOUNT_ID", raising=False)
    with pytest.raises(ComposioConfigError, match="COMPOSIO_LINKEDIN_CONNECTED_ACCOUNT_ID"):
        get_linkedin_connected_account_id()


def test_missing_x_connected_account_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPOSIO_X_CONNECTED_ACCOUNT_ID", raising=False)
    with pytest.raises(ComposioConfigError, match="COMPOSIO_X_CONNECTED_ACCOUNT_ID"):
        get_x_connected_account_id()


def test_entity_id_defaults_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPOSIO_ENTITY_ID", raising=False)
    assert get_composio_entity_id() == "default"


def test_entity_id_is_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSIO_ENTITY_ID", "some-other-entity")
    assert get_composio_entity_id() == "some-other-entity"


async def test_execute_linkedin_action_passes_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSIO_LINKEDIN_CONNECTED_ACCOUNT_ID", "ca_test123")
    monkeypatch.setenv("COMPOSIO_ENTITY_ID", "default")
    captured = {}

    def fake_execute(**kwargs):
        captured.update(kwargs)
        return {"data": {}, "successful": True}

    fake_client = SimpleNamespace(tools=SimpleNamespace(execute=fake_execute))
    monkeypatch.setattr(composio_client, "get_composio_client", lambda: fake_client)

    await execute_linkedin_action("LINKEDIN_CREATE_LINKED_IN_POST", {"author": "urn:li:person:1", "commentary": "hi"})

    assert captured["slug"] == "LINKEDIN_CREATE_LINKED_IN_POST"
    assert captured["arguments"] == {"author": "urn:li:person:1", "commentary": "hi"}
    assert captured["connected_account_id"] == "ca_test123"
    assert captured["user_id"] == "default"


async def test_execute_x_action_passes_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSIO_X_CONNECTED_ACCOUNT_ID", "ca_xtest")
    monkeypatch.setenv("COMPOSIO_ENTITY_ID", "default")
    captured = {}

    def fake_execute(**kwargs):
        captured.update(kwargs)
        return {"data": {}, "successful": True}

    fake_client = SimpleNamespace(tools=SimpleNamespace(execute=fake_execute))
    monkeypatch.setattr(composio_client, "get_composio_client", lambda: fake_client)

    await execute_x_action("X_SOME_ACTION", {"query": "hi"})

    assert captured["connected_account_id"] == "ca_xtest"
    assert captured["user_id"] == "default"


async def test_get_linkedin_author_urn_prefers_author_id(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute_linkedin_action(action_slug: str, arguments: dict) -> dict:
        return {"data": {"response_dict": {"author_id": "urn:li:person:already-a-urn"}}, "successful": True}

    monkeypatch.setattr(composio_client, "execute_linkedin_action", fake_execute_linkedin_action)

    assert await get_linkedin_author_urn() == "urn:li:person:already-a-urn"


async def test_get_linkedin_author_urn_falls_back_to_sub_and_wraps_as_urn(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute_linkedin_action(action_slug: str, arguments: dict) -> dict:
        return {"data": {"response_dict": {"sub": "123456789"}}, "successful": True}

    monkeypatch.setattr(composio_client, "execute_linkedin_action", fake_execute_linkedin_action)

    assert await get_linkedin_author_urn() == "urn:li:person:123456789"


async def test_get_linkedin_author_urn_falls_back_to_direct_data_id(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute_linkedin_action(action_slug: str, arguments: dict) -> dict:
        return {
            "data": {
                "id": "YrJ6jrwmHw",
                "localizedFirstName": "ASWIN",
                "localizedLastName": "S",
            },
            "error": None,
            "successful": True,
        }

    monkeypatch.setattr(composio_client, "execute_linkedin_action", fake_execute_linkedin_action)

    assert await get_linkedin_author_urn() == "urn:li:person:YrJ6jrwmHw"


async def test_get_linkedin_author_urn_raises_when_neither_field_present(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute_linkedin_action(action_slug: str, arguments: dict) -> dict:
        return {"data": {"response_dict": {}}, "successful": True}

    monkeypatch.setattr(composio_client, "execute_linkedin_action", fake_execute_linkedin_action)

    with pytest.raises(ComposioConfigError, match="did not return author_id/sub/id"):
        await get_linkedin_author_urn()


def test_client_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSIO_API_KEY", "test-key")
    first = composio_client.get_composio_client()
    second = composio_client.get_composio_client()
    assert first is second
