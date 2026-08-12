from __future__ import annotations

import typing as t

from app.tools.composio_client import execute_linkedin_action
from app.tools.rate_limit import daily_rate_limiter
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

COMPOSIO_ACTION_SLUG = "LINKEDIN_REPLY_TO_COMMENT"
RATE_LIMIT_ENV_VAR = "LINKEDIN_API_RATE_LIMIT_REPLIES_DAILY"


class ReplyToCommentArgs(BaseModel):
    comment_id: str = Field(..., min_length=1)
    reply_text: str = Field(..., min_length=1)


@registry.register(
    ToolDefinition(
        name="reply_to_comment",
        description="Post a public reply to a comment on the user's LinkedIn post, via Composio",
        requires_approval=True,
    ),
    schema=ReplyToCommentArgs,
)
async def execute(args: ReplyToCommentArgs) -> dict[str, t.Any]:
    daily_rate_limiter.check_and_increment("reply_to_comment_or_dm", RATE_LIMIT_ENV_VAR)
    response = await execute_linkedin_action(
        COMPOSIO_ACTION_SLUG,
        {"comment_id": args.comment_id, "reply_text": args.reply_text},
    )
    return {"comment_id": args.comment_id, "status": "replied", "composio_response": response}
