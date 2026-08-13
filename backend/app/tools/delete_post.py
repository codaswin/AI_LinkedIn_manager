from __future__ import annotations

import typing as t
from datetime import datetime

from app.tools.composio_client import execute_linkedin_action
from app.tools.rate_limit import daily_rate_limiter
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

COMPOSIO_ACTION_SLUG = "LINKEDIN_DELETE_LINKED_IN_POST"
RATE_LIMIT_ENV_VAR = "LINKEDIN_API_RATE_LIMIT_DELETES_DAILY"


class DeletePostArgs(BaseModel):
    # post_content/published_at/engagement_stats are required, not Optional[...] lookups by
    # post_id, per safety requirements: a human approving an irreversible delete must see
    # exactly what they're deleting in the approval payload itself, not trust an opaque ID that
    # a later lookup could resolve to the wrong post (or fail silently). Exactly one post per
    # call — no list/batch field exists here, so bulk delete is structurally impossible.
    #
    # post_id must be LinkedIn's own share identifier (the id/URN Composio's create-post response
    # returns, e.g. from publish_post's composio_response) — not an internally-generated ID — since
    # it's forwarded to Composio as `share_id` (see execute() below, verified against Composio's
    # live LINKEDIN_DELETE_LINKED_IN_POST schema).
    post_id: str = Field(..., min_length=1)
    post_content: str = Field(..., min_length=1)
    published_at: datetime
    engagement_stats: dict[str, int | float] = Field(..., min_length=1)


@registry.register(
    ToolDefinition(
        name="delete_post",
        description="Delete a previously published LinkedIn post, via Composio — irreversible",
        requires_approval=True,
    ),
    schema=DeletePostArgs,
)
async def execute(args: DeletePostArgs) -> dict[str, t.Any]:
    daily_rate_limiter.check_and_increment("delete_post", RATE_LIMIT_ENV_VAR)
    response = await execute_linkedin_action(COMPOSIO_ACTION_SLUG, {"share_id": args.post_id})
    return {
        "post_id": args.post_id,
        "status": "deleted",
        "deleted_post_content": args.post_content,
        "published_at": args.published_at.isoformat(),
        "engagement_stats": args.engagement_stats,
        "composio_response": response,
    }
