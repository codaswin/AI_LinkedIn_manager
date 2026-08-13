from __future__ import annotations

import os
import threading
import typing as t

_ComposioSDK: t.Any = None
try:
    from composio import Composio as _ComposioSDK  # type: ignore[assignment]
except ImportError:
    pass


class ComposioConfigError(RuntimeError):
    """Raised when Composio credentials/config are missing — never proceed with a None client."""


_client_lock = threading.Lock()
_client: t.Any = None


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ComposioConfigError(f"{name} is not set. Set it in the environment before using any Composio-backed tool.")
    return value


def get_composio_client() -> t.Any:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        api_key = _require_env("COMPOSIO_API_KEY")
        if _ComposioSDK is None:
            raise ComposioConfigError(
                "The 'composio' package is not installed. Add it to backend requirements to use Composio-backed tools."
            )
        _client = _ComposioSDK(api_key=api_key)
        return _client


def reset_client_cache() -> None:
    global _client
    with _client_lock:
        _client = None


def get_linkedin_connected_account_id() -> str:
    return _require_env("COMPOSIO_LINKEDIN_CONNECTED_ACCOUNT_ID")


def get_x_connected_account_id() -> str:
    return _require_env("COMPOSIO_X_CONNECTED_ACCOUNT_ID")


def get_composio_entity_id() -> str:
    """The `user_id` Composio's execute API requires alongside connected_account_id.

    Composio calls this the "entity" — an identifier you choose to represent
    an end user when a connected account is created; Composio's own execute
    API rejects a call without it ("User ID is required with connected
    account", verified live). This is a single-user tool, so one constant
    value is enough — but it must match whatever entity_id was used when the
    LinkedIn/X account was connected (see docs/composio-linkedin-setup, or
    whatever created the connection), hence overridable rather than hardcoded.
    """
    return os.environ.get("COMPOSIO_ENTITY_ID", "default")


async def execute_linkedin_action(action_slug: str, arguments: dict[str, t.Any]) -> dict[str, t.Any]:
    client = get_composio_client()
    account_id = get_linkedin_connected_account_id()
    response = client.tools.execute(
        slug=action_slug,
        arguments=arguments,
        connected_account_id=account_id,
        user_id=get_composio_entity_id(),
        # Composio's manual-execution path (this one — not their managed agent
        # runtime) refuses to run without an explicit toolkit version ("latest"
        # isn't accepted here, verified live). Every dated version string is a
        # moving target not worth pinning/tracking for a single-user tool, so
        # this opts out of version pinning entirely (Composio's own suggested
        # fix #4) rather than hardcoding a version that will eventually go stale.
        dangerously_skip_version_check=True,
    )
    return dict(response)


async def execute_x_action(action_slug: str, arguments: dict[str, t.Any]) -> dict[str, t.Any]:
    client = get_composio_client()
    account_id = get_x_connected_account_id()
    response = client.tools.execute(
        slug=action_slug,
        arguments=arguments,
        connected_account_id=account_id,
        user_id=get_composio_entity_id(),
        dangerously_skip_version_check=True,
    )
    return dict(response)


async def get_linkedin_author_urn() -> str:
    """Resolve the connected LinkedIn account's author URN.

    Required by LINKEDIN_CREATE_LINKED_IN_POST's `author` field (verified
    live against Composio's real tool schema — the docs describe it as
    derived from LINKEDIN_GET_MY_INFO's response). Fetched fresh on every
    call rather than cached: this is a cheap, read-only lookup, and a stale
    cached URN across a re-authed/rotated connection is a worse failure mode
    than one extra API call per post.
    """
    response = await execute_linkedin_action("LINKEDIN_GET_MY_INFO", {})
    raw_data = response.get("data") or {}
    data = raw_data.get("response_dict") if isinstance(raw_data, dict) else None
    if not isinstance(data, dict):
        data = raw_data if isinstance(raw_data, dict) else {}

    # Composio has returned both shapes in real calls:
    #   {"data": {"response_dict": {"author_id": ...}}}
    #   {"data": {"id": "YrJ6jrwmHw", ...}}
    # LinkedIn's person id is sufficient once wrapped as urn:li:person:<id>.
    author_id = data.get("author_id") or data.get("sub") or data.get("id")
    if not author_id:
        raise ComposioConfigError(
            "LINKEDIN_GET_MY_INFO did not return author_id/sub/id to build a post's author URN "
            f"from: {response!r}"
        )
    author_id = str(author_id)
    return author_id if author_id.startswith("urn:") else f"urn:li:person:{author_id}"
