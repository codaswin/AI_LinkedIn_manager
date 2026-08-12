from __future__ import annotations

import typing as t
import uuid
from datetime import datetime, timezone

from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field


class DraftPostArgs(BaseModel):
    content: str = Field(..., min_length=1)
    topic: str = Field(..., min_length=1)


@registry.register(
    ToolDefinition(
        name="draft_post",
        description="Create a queued draft post (not published, no external call)",
        requires_approval=False,
    ),
    schema=DraftPostArgs,
)
async def execute(args: DraftPostArgs) -> dict[str, t.Any]:
    # TODO(memory-agent): persist into working/episodic memory once those stores land;
    # queuing in-memory here keeps the tool usable without a hard dependency on that layer.
    post_id = str(uuid.uuid4())
    return {
        "post_id": post_id,
        "status": "draft",
        "topic": args.topic,
        "content": args.content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
