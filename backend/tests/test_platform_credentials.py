from __future__ import annotations

import os

import pytest
from app.llmops import openai_client
from app.memory import platform_credentials
from app.safety import secrets
from app.tools import composio_client
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """save_platform_credentials() mutates os.environ directly (that's the

    point — a saved key must take effect immediately), so monkeypatch's
    normal auto-revert (which only tracks its own setenv/delenv calls)
    can't clean it up on its own. Snapshot the whole environment and
    restore it exactly after each test instead, so nothing this file saves
    (OPENAI_API_KEY, REDDIT_CLIENT_ID, ...) leaks into any other test file.

    Also explicitly clears every env var any platform's schema maps to
    before each test: this repo's own .env (loaded once via config.py's
    load_dotenv() at import time, ambient for the rest of the process) may
    legitimately have some of these set for real local development — a
    test asserting "nothing is configured by default" needs that starting
    from a genuinely clean slate, not whatever the developer's own .env
    happens to contain.
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
def _clear_provider_client_caches() -> None:
    """composio_client/openai_client cache their SDK client as a process-lifetime

    singleton (see reset_client_cache in each module) — leaking a cached
    client across tests would mask the exact regression
    test_save_invalidates_the_cached_composio_client below exists to catch.
    """
    composio_client.reset_client_cache()
    openai_client.reset_client_cache()
    yield
    composio_client.reset_client_cache()
    openai_client.reset_client_cache()


async def test_list_status_shows_everything_unset_by_default(db_session) -> None:
    statuses = await platform_credentials.list_platform_status(db_session)
    ids = {s["id"] for s in statuses}
    assert {"openai", "anthropic", "composio", "linkedin", "reddit", "github"} <= ids

    openai_status = next(s for s in statuses if s["id"] == "openai")
    assert openai_status["connected"] is False
    assert openai_status["fields"][0]["status"] == "not_set"
    assert openai_status["fields"][0]["masked_preview"] is None


async def test_save_single_field_platform_marks_it_connected(db_session) -> None:
    await platform_credentials.save_platform_credentials(db_session, "openai", {"api_key": "sk-abcd1234"})

    statuses = await platform_credentials.list_platform_status(db_session)
    openai_status = next(s for s in statuses if s["id"] == "openai")
    assert openai_status["connected"] is True
    assert openai_status["fields"][0]["status"] == "saved_here"
    assert openai_status["fields"][0]["masked_preview"] == "••••1234"
    assert os.environ["OPENAI_API_KEY"] == "sk-abcd1234"


async def test_save_never_returns_the_raw_value_anywhere(db_session) -> None:
    await platform_credentials.save_platform_credentials(db_session, "openai", {"api_key": "sk-abcd1234"})
    statuses = await platform_credentials.list_platform_status(db_session)
    openai_status = next(s for s in statuses if s["id"] == "openai")
    assert "sk-abcd1234" not in str(openai_status)


async def test_save_rejects_a_partial_multi_field_platform(db_session) -> None:
    with pytest.raises(ValueError, match="client_secret"):
        await platform_credentials.save_platform_credentials(
            db_session, "reddit", {"client_id": "abc", "user_agent": "my-app/1.0"}
        )
    assert not os.environ.get("REDDIT_CLIENT_ID")


async def test_save_full_multi_field_platform_sets_every_env_var(db_session) -> None:
    await platform_credentials.save_platform_credentials(
        db_session,
        "reddit",
        {"client_id": "abc123", "client_secret": "shh-secret", "user_agent": "my-app/1.0"},
    )
    assert os.environ["REDDIT_CLIENT_ID"] == "abc123"
    assert os.environ["REDDIT_CLIENT_SECRET"] == "shh-secret"
    assert os.environ["REDDIT_USER_AGENT"] == "my-app/1.0"

    statuses = await platform_credentials.list_platform_status(db_session)
    reddit_status = next(s for s in statuses if s["id"] == "reddit")
    assert reddit_status["connected"] is True
    client_id_field = next(f for f in reddit_status["fields"] if f["name"] == "client_id")
    assert client_id_field["secret"] is False
    assert client_id_field["masked_preview"] == "abc123"  # non-secret fields shown in full


async def test_field_already_set_via_server_environment_is_distinguished_from_saved(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_setDirectlyInEnv")
    statuses = await platform_credentials.list_platform_status(db_session)
    github_status = next(s for s in statuses if s["id"] == "github")
    assert github_status["connected"] is True
    assert github_status["fields"][0]["status"] == "set_on_server"
    assert github_status["fields"][0]["masked_preview"] is None  # never exposes a value it didn't itself store


async def test_delete_clears_db_and_unsets_env(db_session) -> None:
    await platform_credentials.save_platform_credentials(db_session, "openai", {"api_key": "sk-abcd1234"})
    deleted = await platform_credentials.delete_platform_credentials(db_session, "openai")
    assert deleted is True
    assert "OPENAI_API_KEY" not in os.environ

    statuses = await platform_credentials.list_platform_status(db_session)
    openai_status = next(s for s in statuses if s["id"] == "openai")
    assert openai_status["connected"] is False


async def test_delete_returns_false_when_nothing_was_saved(db_session) -> None:
    assert await platform_credentials.delete_platform_credentials(db_session, "openai") is False


async def test_unknown_platform_raises() -> None:
    with pytest.raises(ValueError, match="Unknown platform"):
        platform_credentials.get_platform_schema("not-a-real-platform")


async def test_load_saved_credentials_into_env_replays_after_env_is_cleared(db_session) -> None:
    await platform_credentials.save_platform_credentials(db_session, "openai", {"api_key": "sk-abcd1234"})
    del os.environ["OPENAI_API_KEY"]  # simulate a fresh process that never ran save() this run

    await platform_credentials.load_saved_credentials_into_env(db_session)

    assert os.environ["OPENAI_API_KEY"] == "sk-abcd1234"


async def test_save_invalidates_the_cached_composio_client(db_session) -> None:
    # Regression test for a production bug: a user pastes a corrected
    # COMPOSIO_API_KEY into the Connections UI, save_platform_credentials()
    # updates os.environ correctly, but the next publish_post still failed
    # with the OLD key's "Invalid API key" 401 — because
    # composio_client.get_composio_client() caches the SDK client object
    # forever once built, and nothing told it to rebuild after a save.
    await platform_credentials.save_platform_credentials(db_session, "composio", {"api_key": "ck_stale_wrong_key"})
    stale_client = composio_client.get_composio_client()

    await platform_credentials.save_platform_credentials(db_session, "composio", {"api_key": "ck_corrected_key"})

    fresh_client = composio_client.get_composio_client()
    assert fresh_client is not stale_client
    assert os.environ["COMPOSIO_API_KEY"] == "ck_corrected_key"


async def test_delete_invalidates_the_cached_composio_client(db_session) -> None:
    await platform_credentials.save_platform_credentials(db_session, "composio", {"api_key": "ck_to_be_removed"})
    composio_client.get_composio_client()  # populate the cache

    await platform_credentials.delete_platform_credentials(db_session, "composio")

    # If the cache weren't invalidated, this would silently hand back the
    # stale client instead of raising for the now-unset env var.
    with pytest.raises(composio_client.ComposioConfigError):
        composio_client.get_composio_client()


async def test_load_saved_credentials_skips_undecryptable_rows_without_raising(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    await platform_credentials.save_platform_credentials(db_session, "openai", {"api_key": "sk-abcd1234"})
    del os.environ["OPENAI_API_KEY"]

    # Simulate a rotated encryption key — old rows are now undecryptable.
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    secrets.reset_for_testing()

    await platform_credentials.load_saved_credentials_into_env(db_session)  # must not raise

    assert "OPENAI_API_KEY" not in os.environ
