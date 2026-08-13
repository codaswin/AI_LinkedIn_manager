from __future__ import annotations

import typing as t

from app.tools.composio_client import execute_x_action
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

# Hard project rule (project rules / safety requirements): no tool in this system may ever
# post/reply/DM/like/retweet/follow on X. This action slug must always resolve to a Composio
# *search/read* scope on the X toolkit — never swap it for a write-scoped slug (CREATE_*,
# POST_*, REPLY_*, DM_*, LIKE_*, RETWEET_*, FOLLOW_*, DELETE_*). test_tools.py asserts this.
COMPOSIO_ACTION_SLUG = "X_SEARCH_RECENT_TWEETS"
COMPOSIO_ACTION_SCOPE: t.Literal["read"] = "read"

_FORBIDDEN_SLUG_TOKENS = (
    "CREATE",
    "POST",
    "REPLY",
    "DM",
    "MESSAGE",
    "LIKE",
    "RETWEET",
    "FOLLOW",
    "DELETE",
    "SEND",
    "PUBLISH",
)


class SearchXPostsArgs(BaseModel):
    """Read-only by construction: every field below only ever narrows a search query.

    No field here may ever accept post/reply/DM content, a recipient, or a target post id to
    act on — adding one would turn this schema into a write path, which is forbidden.
    """

    query: str = Field(..., min_length=1, description="Search terms, e.g. 'agentic AI tooling'")
    max_results: int = Field(default=20, ge=1, le=100)


@registry.register(
    ToolDefinition(
        name="search_x_posts",
        description="Search X (Twitter) via Composio for AI/Agentic AI/Hermes/tooling updates (read-only)",
        requires_approval=False,
    ),
    schema=SearchXPostsArgs,
)
async def execute(args: SearchXPostsArgs) -> dict[str, t.Any]:
    assert COMPOSIO_ACTION_SCOPE == "read"
    assert not any(token in COMPOSIO_ACTION_SLUG for token in _FORBIDDEN_SLUG_TOKENS)

    response = await execute_x_action(
        COMPOSIO_ACTION_SLUG,
        {"query": args.query, "max_results": args.max_results},
    )
    return {"posts": response}
