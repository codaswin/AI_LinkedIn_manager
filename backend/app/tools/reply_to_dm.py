from __future__ import annotations

import typing as t

from app.tools.composio_client import execute_linkedin_action
from app.tools.rate_limit import daily_rate_limiter
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

COMPOSIO_ACTION_SLUG = "LINKEDIN_SEND_MESSAGE"
RATE_LIMIT_ENV_VAR = "LINKEDIN_API_RATE_LIMIT_REPLIES_DAILY"


class ReplyToDmArgs(BaseModel):
    thread_id: str = Field(..., min_length=1)
    message_text: str = Field(..., min_length=1)


@registry.register(
    ToolDefinition(
        name="reply_to_dm",
        description="Send a private message reply to a LinkedIn DM thread, via Composio",
        requires_approval=True,
    ),
    schema=ReplyToDmArgs,
)
async def execute(args: ReplyToDmArgs) -> dict[str, t.Any]:
    # Shares the "replies" daily cap (product spec: <=20 comment/DM replies/day combined) with
    # reply_to_comment — same counter key, same env var.
    daily_rate_limiter.check_and_increment("reply_to_comment_or_dm", RATE_LIMIT_ENV_VAR)
    response = await execute_linkedin_action(
        COMPOSIO_ACTION_SLUG,
        {"thread_id": args.thread_id, "message_text": args.message_text},
    )
    return {"thread_id": args.thread_id, "status": "sent", "composio_response": response}
