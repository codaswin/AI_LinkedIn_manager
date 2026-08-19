from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.tenancy import context as tenancy_context
from app.tenancy import credentials as tenancy_credentials
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

_USER = "composio-test-user"


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


def test_missing_api_key_raises_config_error() -> None:
    with pytest.raises(ComposioConfigError, match="COMPOSIO_API_KEY"):
        composio_client.get_composio_client()


def test_missing_linkedin_connected_account_raises_config_error() -> None:
    with pytest.raises(ComposioConfigError, match="COMPOSIO_LINKEDIN_CONNECTED_ACCOUNT_ID"):
        get_linkedin_connected_account_id()


def test_missing_x_connected_account_raises_config_error() -> None:
    with pytest.raises(ComposioConfigError, match="COMPOSIO_X_CONNECTED_ACCOUNT_ID"):
        get_x_connected_account_id()


def test_entity_id_is_the_current_dashboard_user_id() -> None:
    assert get_composio_entity_id() == _USER


async def test_execute_linkedin_action_passes_user_id() -> None:
    tenancy_credentials.set_credential(_USER, "COMPOSIO_LINKEDIN_CONNECTED_ACCOUNT_ID", "ca_test123")
    captured = {}

    def fake_execute(**kwargs):
        captured.update(kwargs)
        return {"data": {}, "successful": True}

    fake_client = SimpleNamespace(tools=SimpleNamespace(execute=fake_execute))
    composio_client._clients[_USER] = fake_client

    await execute_linkedin_action("LINKEDIN_CREATE_LINKED_IN_POST", {"author": "urn:li:person:1", "commentary": "hi"})

    assert captured["slug"] == "LINKEDIN_CREATE_LINKED_IN_POST"
    assert captured["arguments"] == {"author": "urn:li:person:1", "commentary": "hi"}
    assert captured["connected_account_id"] == "ca_test123"
    assert captured["user_id"] == _USER


async def test_execute_x_action_passes_user_id() -> None:
    tenancy_credentials.set_credential(_USER, "COMPOSIO_X_CONNECTED_ACCOUNT_ID", "ca_xtest")
    captured = {}

    def fake_execute(**kwargs):
        captured.update(kwargs)
        return {"data": {}, "successful": True}

    fake_client = SimpleNamespace(tools=SimpleNamespace(execute=fake_execute))
    composio_client._clients[_USER] = fake_client

    await execute_x_action("X_SOME_ACTION", {"query": "hi"})

    assert captured["connected_account_id"] == "ca_xtest"
    assert captured["user_id"] == _USER


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


def test_client_is_cached_across_calls() -> None:
    tenancy_credentials.set_credential(_USER, "COMPOSIO_API_KEY", "test-key")
    first = composio_client.get_composio_client()
    second = composio_client.get_composio_client()
    assert first is second


def test_client_cache_is_isolated_per_user() -> None:
    other_user = "composio-other-user"
    tenancy_credentials.clear_user(other_user)
    tenancy_credentials.set_credential(_USER, "COMPOSIO_API_KEY", "user-a-key")
    tenancy_credentials.set_credential(other_user, "COMPOSIO_API_KEY", "user-b-key")

    mine = composio_client.get_composio_client()

    token = tenancy_context.set_current_user_id(other_user)
    try:
        theirs = composio_client.get_composio_client()
    finally:
        tenancy_context.reset_current_user_id(token)
        tenancy_credentials.clear_user(other_user)
        composio_client.reset_client_cache(other_user)

    assert mine is not theirs


def test_reset_client_cache_with_user_id_only_clears_that_user() -> None:
    other_user = "composio-scoped-reset-user"
    tenancy_credentials.clear_user(other_user)
    tenancy_credentials.set_credential(_USER, "COMPOSIO_API_KEY", "user-a-key")
    tenancy_credentials.set_credential(other_user, "COMPOSIO_API_KEY", "user-b-key")

    mine_before = composio_client.get_composio_client()
    token = tenancy_context.set_current_user_id(other_user)
    try:
        theirs_before = composio_client.get_composio_client()
    finally:
        tenancy_context.reset_current_user_id(token)

    composio_client.reset_client_cache(_USER)

    mine_after = composio_client.get_composio_client()
    token = tenancy_context.set_current_user_id(other_user)
    try:
        theirs_after = composio_client.get_composio_client()
    finally:
        tenancy_context.reset_current_user_id(token)
        tenancy_credentials.clear_user(other_user)
        composio_client.reset_client_cache(other_user)

    assert mine_after is not mine_before
    assert theirs_after is theirs_before
