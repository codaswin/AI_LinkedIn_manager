from __future__ import annotations

import typing as t

from app.tools.composio_client import execute_linkedin_action, get_linkedin_author_urn
from app.tools.rate_limit import daily_rate_limiter
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

COMPOSIO_ACTION_SLUG = "LINKEDIN_CREATE_LINKED_IN_POST"
RATE_LIMIT_ENV_VAR = "LINKEDIN_API_RATE_LIMIT_POSTS_DAILY"


class PublishPostArgs(BaseModel):
    content: str = Field(..., min_length=1)


@registry.register(
    ToolDefinition(
        name="publish_post",
        description="Publish post content immediately to LinkedIn, via Composio",
        requires_approval=True,
    ),
    schema=PublishPostArgs,
)
async def execute(args: PublishPostArgs) -> dict[str, t.Any]:
    # Reached only after the safety-agent's approval gate passes this call through with
    # approved=True — see registry.execute_tool's ApprovalRequiredError guard.
    #
    # `author` and `commentary` are LINKEDIN_CREATE_LINKED_IN_POST's real required fields —
    # verified live against Composio's own schema (GET /api/v3/tools/LINKEDIN_CREATE_LINKED_IN_POST),
    # not guessed. `author` is a LinkedIn URN (e.g. urn:li:person:...), not free text — see
    # composio_client.get_linkedin_author_urn().
    daily_rate_limiter.check_and_increment("publish_post", RATE_LIMIT_ENV_VAR)
    author = await get_linkedin_author_urn()
    response = await execute_linkedin_action(COMPOSIO_ACTION_SLUG, {"author": author, "commentary": args.content})
    return {"status": "published", "content": args.content, "composio_response": response}
