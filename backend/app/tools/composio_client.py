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


async def execute_linkedin_action(action_slug: str, arguments: dict[str, t.Any]) -> dict[str, t.Any]:
    client = get_composio_client()
    account_id = get_linkedin_connected_account_id()
    response = client.tools.execute(slug=action_slug, arguments=arguments, connected_account_id=account_id)
    return dict(response)


async def execute_x_action(action_slug: str, arguments: dict[str, t.Any]) -> dict[str, t.Any]:
    client = get_composio_client()
    account_id = get_x_connected_account_id()
    response = client.tools.execute(slug=action_slug, arguments=arguments, connected_account_id=account_id)
    return dict(response)
