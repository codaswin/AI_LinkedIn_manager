from __future__ import annotations

import typing as t
from datetime import datetime, timezone

from app.tools.composio_client import execute_linkedin_action
from app.tools.rate_limit import daily_rate_limiter
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field, field_validator

COMPOSIO_ACTION_SLUG = "LINKEDIN_SCHEDULE_LINKED_IN_POST"
RATE_LIMIT_ENV_VAR = "LINKEDIN_API_RATE_LIMIT_POSTS_DAILY"


class SchedulePostArgs(BaseModel):
    post_id: str = Field(..., min_length=1)
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
        description="Queue a draft to auto-publish to LinkedIn at a future time, via Composio + n8n",
        requires_approval=True,
    ),
    schema=SchedulePostArgs,
)
async def execute(args: SchedulePostArgs) -> dict[str, t.Any]:
    daily_rate_limiter.check_and_increment("schedule_post", RATE_LIMIT_ENV_VAR)
    response = await execute_linkedin_action(
        COMPOSIO_ACTION_SLUG,
        {"post_id": args.post_id, "publish_at": args.publish_at.isoformat()},
    )
    return {
        "post_id": args.post_id,
        "status": "scheduled",
        "publish_at": args.publish_at.isoformat(),
        "composio_response": response,
    }
