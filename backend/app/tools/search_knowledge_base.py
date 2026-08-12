from __future__ import annotations

import inspect
import typing as t

from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel, Field

try:
    from app.rag.retrieve import retrieve as _retrieve
except ImportError:
    # TODO(rag-agent): backend/app/rag/retrieve.py not built yet as of this file's creation.
    # Falls back to an empty result set rather than blocking tool registration on it.
    _retrieve = None


class SearchKnowledgeBaseArgs(BaseModel):
    query: str = Field(..., min_length=1, description="Free-text query against the RAG index")
    top_k: int = Field(default=5, ge=1, le=50)


@registry.register(
    ToolDefinition(
        name="search_knowledge_base",
        description="Query the RAG index (brand voice, past posts, news, comment/DM threads, X research notes)",
        requires_approval=False,
    ),
    schema=SearchKnowledgeBaseArgs,
)
async def execute(args: SearchKnowledgeBaseArgs) -> dict[str, t.Any]:
    if _retrieve is None:
        return {"results": [], "warning": "app.rag.retrieve is not available yet"}

    outcome = _retrieve(args.query, top_k=args.top_k)
    if inspect.isawaitable(outcome):
        outcome = await outcome
    return {"results": outcome}
