from __future__ import annotations

import typing as t

from app.tools.composio_client import execute_linkedin_action
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

COMPOSIO_ACTION_SLUG = "LINKEDIN_GET_NOTIFICATIONS"


class GetLinkedInNotificationsArgs(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


@registry.register(
    ToolDefinition(
        name="get_linkedin_notifications",
        description="Poll Composio for LinkedIn comments/DMs/connection requests (read-only)",
        requires_approval=False,
    ),
    schema=GetLinkedInNotificationsArgs,
)
async def execute(args: GetLinkedInNotificationsArgs) -> dict[str, t.Any]:
    response = await execute_linkedin_action(COMPOSIO_ACTION_SLUG, {"limit": args.limit})
    return {"notifications": response}
