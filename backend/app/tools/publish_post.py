from __future__ import annotations

import typing as t

from app.tools.composio_client import execute_linkedin_action
from app.tools.rate_limit import daily_rate_limiter
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

COMPOSIO_ACTION_SLUG = "LINKEDIN_CREATE_LINKED_IN_POST"
RATE_LIMIT_ENV_VAR = "LINKEDIN_API_RATE_LIMIT_POSTS_DAILY"


class PublishPostArgs(BaseModel):
    post_id: str = Field(..., min_length=1)


@registry.register(
    ToolDefinition(
        name="publish_post",
        description="Publish a queued draft immediately to LinkedIn, via Composio",
        requires_approval=True,
    ),
    schema=PublishPostArgs,
)
async def execute(args: PublishPostArgs) -> dict[str, t.Any]:
    # Reached only after the safety-agent's approval gate passes this call through with
    # approved=True — see registry.execute_tool's ApprovalRequiredError guard.
    daily_rate_limiter.check_and_increment("publish_post", RATE_LIMIT_ENV_VAR)
    response = await execute_linkedin_action(COMPOSIO_ACTION_SLUG, {"post_id": args.post_id})
    return {"post_id": args.post_id, "status": "published", "composio_response": response}
