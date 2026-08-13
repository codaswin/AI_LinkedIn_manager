from __future__ import annotations

import typing as t
from datetime import datetime, timezone

from app.tools.composio_client import ComposioConfigError
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field, field_validator


class SchedulePostArgs(BaseModel):
    content: str = Field(..., min_length=1)
    publish_at: datetime

    @field_validator("publish_at")
    @classmethod
    def _must_be_future(cls, value: datetime) -> datetime:
        reference = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if reference <= datetime.now(timezone.utc):
            raise ValueError("publish_at must be in the future")
        return value


@registry.register(
    ToolDefinition(
        name="schedule_post",
        description="Queue post content to auto-publish to LinkedIn at a future time",
        requires_approval=True,
    ),
    schema=SchedulePostArgs,
)
async def execute(args: SchedulePostArgs) -> dict[str, t.Any]:
    # Composio's LinkedIn toolkit has no scheduling action — confirmed live by
    # listing every tool under the linkedin toolkit (GET /api/v3/tools?toolkit_slug=linkedin):
    # only LINKEDIN_CREATE_LINKED_IN_POST, _DELETE_LINKED_IN_POST, _GET_COMPANY_INFO, and
    # _GET_MY_INFO exist. There is currently no backend that can actually delay this post's
    # publication, so this raises rather than either posting immediately (surprising — the human
    # approved a *scheduled* post) or silently reporting success. Real support needs a stored
    # queue plus a periodic job (e.g. extending learning/scheduler.py's APScheduler) to publish
    # due posts via publish_post's own path — not built here since that's new infrastructure,
    # not a bug fix.
    raise ComposioConfigError(
        "schedule_post has no working backend yet — Composio's LinkedIn integration has no "
        "scheduling action. Use publish_post to post now instead, or ask for real scheduling "
        "support to be built."
    )
