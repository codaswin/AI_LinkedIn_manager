from __future__ import annotations

import typing as t

from app.tools.composio_client import execute_linkedin_action
from app.tools.rate_limit import daily_rate_limiter
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

COMPOSIO_ACTION_SLUG = "LINKEDIN_SEND_CONNECTION_REQUEST"
RATE_LIMIT_ENV_VAR = "LINKEDIN_API_RATE_LIMIT_CONNECTIONS_DAILY"


class SendConnectionRequestArgs(BaseModel):
    profile_id: str = Field(..., min_length=1)
    note: str | None = Field(default=None, max_length=300)


@registry.register(
    ToolDefinition(
        name="send_connection_request",
        description="Send a LinkedIn connection request to another user, via Composio",
        requires_approval=True,
    ),
    schema=SendConnectionRequestArgs,
)
async def execute(args: SendConnectionRequestArgs) -> dict[str, t.Any]:
    daily_rate_limiter.check_and_increment("send_connection_request", RATE_LIMIT_ENV_VAR)
    response = await execute_linkedin_action(
        COMPOSIO_ACTION_SLUG,
        {"profile_id": args.profile_id, "note": args.note},
    )
    return {"profile_id": args.profile_id, "status": "requested", "composio_response": response}
