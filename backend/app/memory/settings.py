from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_setting import AgentSetting

DEFAULT_SETTINGS: dict[str, str] = {
    "research_agent.poll_interval": "daily",
}


async def ensure_default_settings(db: AsyncSession, updated_by: str = "system:seed") -> None:
    """Idempotently seeds agent_settings with DEFAULT_SETTINGS — safe to call on every startup."""
    for key, value in DEFAULT_SETTINGS.items():
        existing = await db.get(AgentSetting, key)
        if existing is None:
            db.add(AgentSetting(key=key, value=value, updated_by=updated_by))
    await db.commit()


async def get_setting(db: AsyncSession, key: str) -> str | None:
    setting = await db.get(AgentSetting, key)
    if setting is not None:
        return setting.value
    # Falls back to the in-code default even if the seed row hasn't been written yet, so
    # `research_agent.poll_interval` reads "daily" out of the box rather than None/KeyError.
    return DEFAULT_SETTINGS.get(key)


async def set_setting(db: AsyncSession, key: str, value: str, updated_by: str) -> AgentSetting:
    setting = await db.get(AgentSetting, key)
    now = datetime.now(timezone.utc)
    if setting is None:
        setting = AgentSetting(key=key, value=value, updated_by=updated_by, updated_at=now)
        db.add(setting)
    else:
        setting.value = value
        setting.updated_by = updated_by
        setting.updated_at = now
    await db.commit()
    await db.refresh(setting)
    return setting
