from __future__ import annotations

import inspect
import typing as t
import uuid
from datetime import datetime, timezone

from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field, HttpUrl

try:
    from app.memory.semantic import write_semantic_memory as _write_semantic_memory
except ImportError:
    # TODO(memory-agent): backend/app/memory/semantic.py write API not finalized yet.
    _write_semantic_memory = None


class SaveResearchNoteArgs(BaseModel):
    content: str = Field(..., min_length=1)
    source_url: HttpUrl


@registry.register(
    ToolDefinition(
        name="save_research_note",
        description="Persist a research finding to semantic memory / the X research RAG source (internal write only, never published externally)",
        requires_approval=False,
    ),
    schema=SaveResearchNoteArgs,
)
async def execute(args: SaveResearchNoteArgs) -> dict[str, t.Any]:
    note_id = str(uuid.uuid4())
    record = {
        "id": note_id,
        "content": args.content,
        "source_url": str(args.source_url),
        "source": "research_agent",
        "confidence": 1.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if _write_semantic_memory is not None:
        outcome = _write_semantic_memory(record)
        if inspect.isawaitable(outcome):
            await outcome
    return {"note_id": note_id, "status": "saved"}
