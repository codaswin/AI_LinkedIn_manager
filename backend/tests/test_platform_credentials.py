from __future__ import annotations

import os

import pytest
from app.llmops import openai_client
from app.memory import platform_credentials
from app.safety import secrets
from app.tenancy import context as tenancy_context
from app.tenancy import credentials as tenancy_credentials
from app.tools import composio_client
from cryptography.fernet import Fernet

_USER = "credentials-test-user"
_OTHER_USER = "credentials-test-other-user"


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the "hermes" platform still reads/writes real os.environ (it's

    shared deployment config, not a per-user secret — see _GLOBAL_PLATFORMS
    in platform_credentials.py). Every other platform now goes through the
    per-user overlay in app.tenancy.credentials, cleared by
    _tenancy_context below instead. This fixture still snapshots/restores
    real os.environ and clears every schema env var up front, since hermes
    and CREDENTIAL_ENCRYPTION_KEY both still live there.
    """
    snapshot = dict(os.environ)
    for platform in platform_credentials.PLATFORM_SCHEMA:
        for f in platform.fields:
            monkeypatch.delenv(f.env_var, raising=False)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    secrets.reset_for_testing()
    yield
    os.environ.clear()
    os.environ.update(snapshot)
    secrets.reset_for_testing()


@pytest.fixture(autouse=True)
def _tenancy_context() -> None:
    tenancy_credentials.clear_user(_USER)
    tenancy_credentials.clear_user(_OTHER_USER)
    token = tenancy_context.set_current_user_id(_USER)
    yield
    tenancy_context.reset_current_user_id(token)
    tenancy_credentials.clear_user(_USER)
    tenancy_credentials.clear_user(_OTHER_USER)


@pytest.fixture(autouse=True)
def _clear_provider_client_caches() -> None:
    """composio_client/openai_client cache their SDK client as a process-lifetime,

    per-user singleton (see reset_client_cache in each module) — leaking a
    cached client across tests would mask the exact regression
    test_save_invalidates_the_cached_composio_client below exists to catch.
    """
    composio_client.reset_client_cache()
    openai_client.reset_client_cache()
    yield
    composio_client.reset_client_cache()
    openai_client.reset_client_cache()


async def test_list_status_shows_everything_unset_by_default(db_session) -> None:
    statuses = await platform_credentials.list_platform_status(db_session, _USER)
    ids = {s["id"] for s in statuses}
    assert {"openai", "anthropic", "composio", "linkedin", "reddit", "github"} <= ids

    openai_status = next(s for s in statuses if s["id"] == "openai")
    assert openai_status["connected"] is False
    assert openai_status["fields"][0]["status"] == "not_set"
    assert openai_status["fields"][0]["masked_preview"] is None


async def test_save_single_field_platform_marks_it_connected(db_session) -> None:
    await platform_credentials.save_platform_credentials(db_session, _USER, "openai", {"api_key": "sk-abcd1234"})

    statuses = await platform_credentials.list_platform_status(db_session, _USER)
    openai_status = next(s for s in statuses if s["id"] == "openai")
    assert openai_status["connected"] is True
    assert openai_status["fields"][0]["status"] == "saved_here"
    assert openai_status["fields"][0]["masked_preview"] == "••••1234"
    assert tenancy_credentials.resolve_credential("OPENAI_API_KEY") == "sk-abcd1234"


async def test_save_never_returns_the_raw_value_anywhere(db_session) -> None:
    await platform_credentials.save_platform_credentials(db_session, _USER, "openai", {"api_key": "sk-abcd1234"})
    statuses = await platform_credentials.list_platform_status(db_session, _USER)
    openai_status = next(s for s in statuses if s["id"] == "openai")
    assert "sk-abcd1234" not in str(openai_status)


async def test_save_rejects_a_partial_multi_field_platform(db_session) -> None:
    with pytest.raises(ValueError, match="client_secret"):
        await platform_credentials.save_platform_credentials(
            db_session, _USER, "reddit", {"client_id": "abc", "user_agent": "my-app/1.0"}
        )
    assert not tenancy_credentials.resolve_credential("REDDIT_CLIENT_ID")


async def test_save_full_multi_field_platform_sets_every_env_var(db_session) -> None:
    await platform_credentials.save_platform_credentials(
        db_session,
        _USER,
        "reddit",
        {"client_id": "abc123", "client_secret": "shh-secret", "user_agent": "my-app/1.0"},
    )
    assert tenancy_credentials.resolve_credential("REDDIT_CLIENT_ID") == "abc123"
    assert tenancy_credentials.resolve_credential("REDDIT_CLIENT_SECRET") == "shh-secret"
    assert tenancy_credentials.resolve_credential("REDDIT_USER_AGENT") == "my-app/1.0"

    statuses = await platform_credentials.list_platform_status(db_session, _USER)
    reddit_status = next(s for s in statuses if s["id"] == "reddit")
    assert reddit_status["connected"] is True
    client_id_field = next(f for f in reddit_status["fields"] if f["name"] == "client_id")
    assert client_id_field["secret"] is False
    assert client_id_field["masked_preview"] == "abc123"  # non-secret fields shown in full


async def test_saved_credentials_are_isolated_per_user(db_session) -> None:
    # The core multi-tenant guarantee: two different dashboard users saving
    # the same platform+field never see or overwrite each other's value —
    # neither in the per-user overlay nor in list_platform_status.
    await platform_credentials.save_platform_credentials(db_session, _USER, "openai", {"api_key": "sk-user-a"})

    token = tenancy_context.set_current_user_id(_OTHER_USER)
    try:
        await platform_credentials.save_platform_credentials(db_session, _OTHER_USER, "openai", {"api_key": "sk-user-b"})
        other_statuses = await platform_credentials.list_platform_status(db_session, _OTHER_USER)
        assert tenancy_credentials.resolve_credential("OPENAI_API_KEY") == "sk-user-b"
    finally:
        tenancy_context.reset_current_user_id(token)

    my_statuses = await platform_credentials.list_platform_status(db_session, _USER)
    assert tenancy_credentials.resolve_credential("OPENAI_API_KEY") == "sk-user-a"

    my_openai = next(s for s in my_statuses if s["id"] == "openai")
    other_openai = next(s for s in other_statuses if s["id"] == "openai")
    assert my_openai["fields"][0]["masked_preview"] == "••••er-a"
    assert other_openai["fields"][0]["masked_preview"] == "••••er-b"
    assert my_openai["connected"] is True
    assert other_openai["connected"] is True


async def test_hermes_platform_stays_a_shared_server_setting(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    # hermes is the one platform that's deployment-wide config, not a
    # per-user secret (see _GLOBAL_PLATFORMS in platform_credentials.py) —
    # it's the only platform where "set_on_server" (os.environ, not this
    # UI) is still a real, reachable status.
    monkeypatch.setenv("HERMES_ENDPOINT", "http://worker.internal:8001/v1")
    statuses = await platform_credentials.list_platform_status(db_session, _USER)
    hermes_status = next(s for s in statuses if s["id"] == "hermes")
    endpoint_field = next(f for f in hermes_status["fields"] if f["name"] == "endpoint")
    assert endpoint_field["status"] == "set_on_server"
    assert endpoint_field["masked_preview"] is None

    await platform_credentials.save_platform_credentials(
        db_session, _USER, "hermes", {"endpoint": "http://worker.internal:9000/v1", "model": "hermes-3"}
    )
    assert os.environ["HERMES_ENDPOINT"] == "http://worker.internal:9000/v1"
    # A different user sees the same shared os.environ value take effect —
    # reported as "set_on_server" for them specifically, since the saved DB
    # row itself belongs to _USER, not them; the point is the *value* isn't
    # isolated per user the way every other platform's is.
    token = tenancy_context.set_current_user_id(_OTHER_USER)
    try:
        other_statuses = await platform_credentials.list_platform_status(db_session, _OTHER_USER)
    finally:
        tenancy_context.reset_current_user_id(token)
    other_hermes = next(s for s in other_statuses if s["id"] == "hermes")
    assert next(f for f in other_hermes["fields"] if f["name"] == "endpoint")["status"] == "set_on_server"


async def test_delete_clears_db_and_unsets_env(db_session) -> None:
    await platform_credentials.save_platform_credentials(db_session, _USER, "openai", {"api_key": "sk-abcd1234"})
    deleted = await platform_credentials.delete_platform_credentials(db_session, _USER, "openai")
    assert deleted is True
    assert tenancy_credentials.resolve_credential("OPENAI_API_KEY") is None

    statuses = await platform_credentials.list_platform_status(db_session, _USER)
    openai_status = next(s for s in statuses if s["id"] == "openai")
    assert openai_status["connected"] is False


async def test_delete_returns_false_when_nothing_was_saved(db_session) -> None:
    assert await platform_credentials.delete_platform_credentials(db_session, _USER, "openai") is False


async def test_unknown_platform_raises() -> None:
    with pytest.raises(ValueError, match="Unknown platform"):
        platform_credentials.get_platform_schema("not-a-real-platform")


async def test_load_all_saved_credentials_replays_after_overlay_is_cleared(db_session) -> None:
    await platform_credentials.save_platform_credentials(db_session, _USER, "openai", {"api_key": "sk-abcd1234"})
    tenancy_credentials.clear_user(_USER)  # simulate a fresh process that never ran save() this run

    await platform_credentials.load_all_saved_credentials(db_session)

    assert tenancy_credentials.resolve_credential("OPENAI_API_KEY") == "sk-abcd1234"


async def test_load_all_saved_credentials_keeps_different_users_isolated(db_session) -> None:
    await platform_credentials.save_platform_credentials(db_session, _USER, "openai", {"api_key": "sk-user-a"})
    token = tenancy_context.set_current_user_id(_OTHER_USER)
    try:
        await platform_credentials.save_platform_credentials(db_session, _OTHER_USER, "openai", {"api_key": "sk-user-b"})
    finally:
        tenancy_context.reset_current_user_id(token)

    tenancy_credentials.clear_user(_USER)
    tenancy_credentials.clear_user(_OTHER_USER)
    await platform_credentials.load_all_saved_credentials(db_session)

    assert tenancy_credentials.resolve_credential("OPENAI_API_KEY") == "sk-user-a"
    token = tenancy_context.set_current_user_id(_OTHER_USER)
    try:
        assert tenancy_credentials.resolve_credential("OPENAI_API_KEY") == "sk-user-b"
    finally:
        tenancy_context.reset_current_user_id(token)


async def test_save_invalidates_the_cached_composio_client(db_session) -> None:
    # Regression test for a production bug: a user pastes a corrected
    # COMPOSIO_API_KEY into the Connections UI, save_platform_credentials()
    # updates the per-user credential overlay correctly, but the next
    # publish_post still failed with the OLD key's "Invalid API key" 401 —
    # because composio_client.get_composio_client() caches the SDK client
    # object forever once built, and nothing told it to rebuild after a save.
    await platform_credentials.save_platform_credentials(db_session, _USER, "composio", {"api_key": "ck_stale_wrong_key"})
    stale_client = composio_client.get_composio_client()

    await platform_credentials.save_platform_credentials(db_session, _USER, "composio", {"api_key": "ck_corrected_key"})

    fresh_client = composio_client.get_composio_client()
    assert fresh_client is not stale_client
    assert tenancy_credentials.resolve_credential("COMPOSIO_API_KEY") == "ck_corrected_key"


async def test_delete_invalidates_the_cached_composio_client(db_session) -> None:
    await platform_credentials.save_platform_credentials(db_session, _USER, "composio", {"api_key": "ck_to_be_removed"})
    composio_client.get_composio_client()  # populate the cache

    await platform_credentials.delete_platform_credentials(db_session, _USER, "composio")

    # If the cache weren't invalidated, this would silently hand back the
    # stale client instead of raising for the now-unset credential.
    with pytest.raises(composio_client.ComposioConfigError):
        composio_client.get_composio_client()


async def test_load_saved_credentials_skips_undecryptable_rows_without_raising(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    await platform_credentials.save_platform_credentials(db_session, _USER, "openai", {"api_key": "sk-abcd1234"})
    tenancy_credentials.clear_user(_USER)

    # Simulate a rotated encryption key — old rows are now undecryptable.
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    secrets.reset_for_testing()

    await platform_credentials.load_all_saved_credentials(db_session)  # must not raise

    assert tenancy_credentials.resolve_credential("OPENAI_API_KEY") is None
