from __future__ import annotations

import typing as t

from app.tools.composio_client import execute_linkedin_action
from app.tools.rate_limit import daily_rate_limiter
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

COMPOSIO_ACTION_SLUG = "LINKEDIN_LIKE_POST"

# The PRP's SAFETY REQUIREMENTS section gives explicit daily caps for connections/replies/
# posts/deletes but names no number for likes. Rather than invent one, this reads a dedicated
# env var (following the same LINKEDIN_API_RATE_LIMIT_*_DAILY convention) with no in-code
# default: if it's unset, the tool fails closed instead of running unrate-limited.
RATE_LIMIT_ENV_VAR = "LINKEDIN_API_RATE_LIMIT_LIKES_DAILY"


class LikePostArgs(BaseModel):
    post_id: str = Field(..., min_length=1)


@registry.register(
    ToolDefinition(
        name="like_post",
        description="Like a LinkedIn post as the user, rate-capped, via Composio",
        requires_approval=False,
    ),
    schema=LikePostArgs,
)
async def execute(args: LikePostArgs) -> dict[str, t.Any]:
    daily_rate_limiter.check_and_increment("like_post", RATE_LIMIT_ENV_VAR)
    response = await execute_linkedin_action(COMPOSIO_ACTION_SLUG, {"post_id": args.post_id})
    return {"post_id": args.post_id, "status": "liked", "composio_response": response}
