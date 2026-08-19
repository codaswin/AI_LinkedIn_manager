from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Literal, TypedDict

from app.llmops import anthropic_client, openai_client
from app.models.platform_credential import PlatformCredentialRecord
from app.safety.secrets import CredentialEncryptionError, decrypt_secret, encrypt_secret, mask_secret
from app.tenancy import credentials as tenancy_credentials
from app.tools import composio_client
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

# Each of these providers caches its SDK client as a process-lifetime,
# per-user singleton the first time it's built for that user (see
# reset_client_cache in each module) — a save/delete here must invalidate
# the matching user's cache entry, or a corrected key silently keeps
# failing with the stale one baked into the old client object until the
# process restarts.
_CLIENT_CACHE_RESETTERS: dict[str, Callable[[str], None]] = {
    "composio": composio_client.reset_client_cache,
    "openai": openai_client.reset_client_cache,
    "anthropic": anthropic_client.reset_client_cache,
}

# Deployment-level config, not a per-user secret — one Hermes/vLLM worker
# endpoint serves the whole deployment, unlike every other platform here
# (LinkedIn/Composio/OpenAI/Anthropic/research sources), which are each
# pasted and owned by an individual dashboard user. This platform alone
# keeps reading/writing real process os.environ instead of the per-user
# credential overlay in app.tenancy.credentials.
_GLOBAL_PLATFORMS = frozenset({"hermes"})

CredentialType = Literal["api_key", "token", "oauth_connected_account", "client_credentials", "endpoint"]
FieldStatus = Literal["not_set", "saved_here", "set_on_server"]


class CredentialFieldStatus(TypedDict):
    name: str
    label: str
    secret: bool
    placeholder: str
    status: FieldStatus
    masked_preview: str | None


class PlatformStatus(TypedDict):
    id: str
    name: str
    group: str
    credential_type: CredentialType
    summary: str
    help_text: str
    required: bool
    connected: bool
    fields: list[CredentialFieldStatus]


@dataclass(frozen=True)
class CredentialField:
    name: str
    env_var: str
    label: str
    placeholder: str
    secret: bool = True


@dataclass(frozen=True)
class PlatformDefinition:
    id: str
    name: str
    group: str
    credential_type: CredentialType
    summary: str
    help_text: str
    required: bool
    fields: tuple[CredentialField, ...]


PLATFORM_SCHEMA: tuple[PlatformDefinition, ...] = (
    PlatformDefinition(
        id="linkedin",
        name="LinkedIn account",
        group="Publishing to LinkedIn",
        credential_type="oauth_connected_account",
        summary="Composio connected-account ID used for LinkedIn publishing, replies, DMs, and connection requests.",
        help_text="Create/connect the LinkedIn account in Composio, then paste the connected account ID here.",
        required=True,
        fields=(CredentialField("connected_account_id", "COMPOSIO_LINKEDIN_CONNECTED_ACCOUNT_ID", "Connected account ID", "ca_xxx"),),
    ),
    PlatformDefinition(
        id="composio",
        name="Composio",
        group="Publishing to LinkedIn",
        credential_type="api_key",
        summary="API key for executing LinkedIn/X toolkit actions through Composio.",
        help_text="Required before any approved LinkedIn action can execute.",
        required=True,
        fields=(CredentialField("api_key", "COMPOSIO_API_KEY", "API key", "composio_xxx"),),
    ),
    PlatformDefinition(
        id="openai",
        name="OpenAI",
        group="AI models",
        credential_type="api_key",
        summary="Hosted model provider when LLM_PROVIDER=openai or when only OPENAI_API_KEY is present.",
        help_text="Optional if Anthropic is configured. Saved values take effect immediately for this process.",
        required=False,
        fields=(CredentialField("api_key", "OPENAI_API_KEY", "API key", "sk-..."),),
    ),
    PlatformDefinition(
        id="anthropic",
        name="Anthropic",
        group="AI models",
        credential_type="api_key",
        summary="Hosted model provider for primary and cheap tiers unless OpenAI is selected.",
        help_text="Optional if OpenAI is configured. Saved values take effect immediately for this process.",
        required=False,
        fields=(CredentialField("api_key", "ANTHROPIC_API_KEY", "API key", "sk-ant-..."),),
    ),
    PlatformDefinition(
        id="hermes",
        name="Hermes/vLLM worker",
        group="AI models",
        credential_type="endpoint",
        summary="OpenAI-compatible endpoint for worker-tier triage calls.",
        help_text="Defaults to the local vLLM endpoint if unset. Shared deployment setting, not per-user.",
        required=False,
        fields=(
            CredentialField("endpoint", "HERMES_ENDPOINT", "Endpoint URL", "http://localhost:8001/v1", secret=False),
            CredentialField("model", "HERMES_MODEL", "Model name", "worker model name", secret=False),
        ),
    ),
    PlatformDefinition(
        id="reddit",
        name="Reddit",
        group="Research sources",
        credential_type="client_credentials",
        summary="OAuth client credentials for subreddit and sitewide research.",
        help_text="Create a Reddit script app and provide its client ID, secret, and a descriptive user agent.",
        required=False,
        fields=(
            CredentialField("client_id", "REDDIT_CLIENT_ID", "Client ID", "client id", secret=False),
            CredentialField("client_secret", "REDDIT_CLIENT_SECRET", "Client secret", "client secret"),
            CredentialField("user_agent", "REDDIT_USER_AGENT", "User agent", "ai-linkedin-manager-research-agent/1.0", secret=False),
        ),
    ),
    PlatformDefinition(
        id="github",
        name="GitHub",
        group="Research sources",
        credential_type="token",
        summary="Optional token for higher GitHub search rate limits.",
        help_text="Unauthenticated GitHub search works at a lower rate limit; add a token for reliability.",
        required=False,
        fields=(CredentialField("token", "GITHUB_TOKEN", "Token", "ghp_..."),),
    ),
    PlatformDefinition(
        id="producthunt",
        name="Product Hunt",
        group="Research sources",
        credential_type="token",
        summary="GraphQL v2 token for product-launch research.",
        help_text="Required only if you want Product Hunt included in research runs.",
        required=False,
        fields=(CredentialField("token", "PRODUCTHUNT_TOKEN", "Token", "ph_..."),),
    ),
    PlatformDefinition(
        id="brave_search",
        name="Brave Search",
        group="Research sources",
        credential_type="api_key",
        summary="Optional API key for the Brave web-search research source (used when WEB_SEARCH_PROVIDER=brave).",
        help_text="Leave empty to use the no-key-required DuckDuckGo provider instead.",
        required=False,
        fields=(CredentialField("api_key", "BRAVE_SEARCH_API_KEY", "API key", "brave_xxx"),),
    ),
    PlatformDefinition(
        id="x",
        name="X / Twitter",
        group="Research sources",
        credential_type="oauth_connected_account",
        summary="Optional Composio connected-account ID for X research. This source is opt-in only.",
        help_text="Leave empty unless you explicitly enable the X research source.",
        required=False,
        fields=(CredentialField("connected_account_id", "COMPOSIO_X_CONNECTED_ACCOUNT_ID", "Connected account ID", "ca_xxx"),),
    ),
)

_PLATFORMS_BY_ID = {p.id: p for p in PLATFORM_SCHEMA}


def _record_id(user_id: str, platform_id: str, field_name: str) -> str:
    return f"{user_id}:{platform_id}:{field_name}"


def _get_platform(platform_id: str) -> PlatformDefinition:
    try:
        return _PLATFORMS_BY_ID[platform_id]
    except KeyError as exc:
        raise ValueError(f"Unknown platform {platform_id!r}") from exc


def _field_for(platform: PlatformDefinition, field_name: str) -> CredentialField | None:
    return next((f for f in platform.fields if f.name == field_name), None)


def get_platform_schema(platform_id: str) -> PlatformDefinition:
    return _get_platform(platform_id)


def _preview_value(value: str, *, secret: bool) -> str:
    return mask_secret(value) if secret else value


async def _records_for_platforms(db: AsyncSession, user_id: str) -> dict[str, PlatformCredentialRecord]:
    result = await db.execute(select(PlatformCredentialRecord).where(PlatformCredentialRecord.user_id == user_id))
    return {record.id: record for record in result.scalars().all()}


async def list_platform_status(db: AsyncSession, user_id: str) -> list[PlatformStatus]:
    records = await _records_for_platforms(db, user_id)
    statuses: list[PlatformStatus] = []
    for platform in PLATFORM_SCHEMA:
        is_global = platform.id in _GLOBAL_PLATFORMS
        fields: list[CredentialFieldStatus] = []
        for field in platform.fields:
            record = records.get(_record_id(user_id, platform.id, field.name))
            if record is not None:
                status: FieldStatus = "saved_here"
                masked_preview = record.masked_preview
            elif is_global and os.environ.get(field.env_var):
                status = "set_on_server"
                masked_preview = None
            else:
                status = "not_set"
                masked_preview = None
            fields.append({
                "name": field.name,
                "label": field.label,
                "secret": field.secret,
                "placeholder": field.placeholder,
                "status": status,
                "masked_preview": masked_preview,
            })
        statuses.append({
            "id": platform.id,
            "name": platform.name,
            "group": platform.group,
            "credential_type": platform.credential_type,
            "summary": platform.summary,
            "help_text": platform.help_text,
            "required": platform.required,
            "connected": all(field["status"] != "not_set" for field in fields),
            "fields": fields,
        })
    return statuses


async def save_platform_credentials(db: AsyncSession, user_id: str, platform_id: str, values: dict[str, str]) -> None:
    platform = _get_platform(platform_id)
    expected_fields = {field.name for field in platform.fields}
    missing = [field.name for field in platform.fields if not values.get(field.name, "").strip()]
    extra = sorted(set(values) - expected_fields)
    if missing:
        raise ValueError(f"Missing required credential field(s) for {platform_id!r}: {', '.join(missing)}")
    if extra:
        raise ValueError(f"Unknown credential field(s) for {platform_id!r}: {', '.join(extra)}")

    is_global = platform_id in _GLOBAL_PLATFORMS
    await db.execute(
        delete(PlatformCredentialRecord).where(
            PlatformCredentialRecord.user_id == user_id, PlatformCredentialRecord.platform_id == platform_id
        )
    )
    for field in platform.fields:
        raw_value = values[field.name].strip()
        db.add(PlatformCredentialRecord(
            id=_record_id(user_id, platform_id, field.name),
            user_id=user_id,
            platform_id=platform_id,
            field_name=field.name,
            encrypted_value=encrypt_secret(raw_value),
            masked_preview=_preview_value(raw_value, secret=field.secret),
        ))
        if is_global:
            os.environ[field.env_var] = raw_value
        else:
            tenancy_credentials.set_credential(user_id, field.env_var, raw_value)
    await db.commit()
    resetter = _CLIENT_CACHE_RESETTERS.get(platform_id)
    if resetter is not None:
        resetter(user_id)


async def delete_platform_credentials(db: AsyncSession, user_id: str, platform_id: str) -> bool:
    platform = _get_platform(platform_id)
    is_global = platform_id in _GLOBAL_PLATFORMS
    result = await db.execute(
        delete(PlatformCredentialRecord).where(
            PlatformCredentialRecord.user_id == user_id, PlatformCredentialRecord.platform_id == platform_id
        )
    )
    await db.commit()
    for field in platform.fields:
        if is_global:
            os.environ.pop(field.env_var, None)
        else:
            tenancy_credentials.clear_credential(user_id, field.env_var)
    resetter = _CLIENT_CACHE_RESETTERS.get(platform_id)
    if resetter is not None:
        resetter(user_id)
    return bool(result.rowcount)


async def load_all_saved_credentials(db: AsyncSession) -> None:
    """Replay every saved credential for every user back into the per-user

    overlay (app.tenancy.credentials) — the DB rows are the durable copy,
    but every non-global credential consumer reads the in-process overlay,
    not the DB, so a process restart needs this to not lose what was
    configured. Global platforms (see _GLOBAL_PLATFORMS) are replayed
    straight into real os.environ instead, matching how they're read.
    """
    result = await db.execute(select(PlatformCredentialRecord))
    values_by_user: dict[str, dict[str, str]] = {}
    for record in result.scalars().all():
        platform = _PLATFORMS_BY_ID.get(record.platform_id)
        field = _field_for(platform, record.field_name) if platform is not None else None
        if platform is None or field is None:
            continue
        try:
            value = decrypt_secret(record.encrypted_value)
        except CredentialEncryptionError:
            # A rotated/missing key must not prevent app startup. The row
            # remains visible as saved in the Connections UI so a human can
            # delete/re-save it with the current key.
            continue
        if platform.id in _GLOBAL_PLATFORMS:
            os.environ[field.env_var] = value
        else:
            values_by_user.setdefault(record.user_id, {})[field.env_var] = value
    tenancy_credentials.load_overlay(values_by_user)
