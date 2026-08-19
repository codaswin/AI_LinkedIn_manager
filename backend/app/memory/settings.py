from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_setting import AgentSetting
from app.tenancy.context import get_current_user_id

DEFAULT_SETTINGS: dict[str, str] = {
    "research_agent.poll_interval": "daily",
}

# Per-user automation topic list for the research scheduler job — no
# in-code default (unlike poll_interval): a user who hasn't configured this
# yet should be skipped by the job, not run against someone else's default
# topics (see app.learning.scheduler._run_research_job).
RESEARCH_AUTOMATION_QUERIES_KEY = "research_agent.automation_queries"


async def ensure_default_settings(db: AsyncSession, updated_by: str = "system:seed") -> None:
    """Idempotently seeds the current user's agent_settings with DEFAULT_SETTINGS — safe to call on every startup/login."""
    user_id = get_current_user_id()
    for key, value in DEFAULT_SETTINGS.items():
        existing = await db.get(AgentSetting, (user_id, key))
        if existing is None:
            db.add(AgentSetting(user_id=user_id, key=key, value=value, updated_by=updated_by))
    await db.commit()


async def get_setting(db: AsyncSession, key: str) -> str | None:
    setting = await db.get(AgentSetting, (get_current_user_id(), key))
    if setting is not None:
        return setting.value
    # Falls back to the in-code default even if the seed row hasn't been written yet, so
    # `research_agent.poll_interval` reads "daily" out of the box rather than None/KeyError.
    return DEFAULT_SETTINGS.get(key)


async def set_setting(db: AsyncSession, key: str, value: str, updated_by: str) -> AgentSetting:
    user_id = get_current_user_id()
    setting = await db.get(AgentSetting, (user_id, key))
    now = datetime.now(timezone.utc)
    if setting is None:
        setting = AgentSetting(user_id=user_id, key=key, value=value, updated_by=updated_by, updated_at=now)
        db.add(setting)
    else:
        setting.value = value
        setting.updated_by = updated_by
        setting.updated_at = now
    await db.commit()
    await db.refresh(setting)
    return setting
