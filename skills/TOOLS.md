# Tools Skill

Schema-validated, sandboxed, logged tool execution. No tool runs without a schema; no risky tool runs without a gate.

## Tool Definition Pattern
```python
# tools/registry.py
from pydantic import BaseModel
from typing import Callable
import time
import structlog

logger = structlog.get_logger()


class ToolDefinition(BaseModel):
    name: str
    description: str
    requires_approval: bool = False
    timeout_seconds: int = 10


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, tuple[ToolDefinition, Callable, type[BaseModel]]] = {}

    def register(self, definition: ToolDefinition, schema: type[BaseModel]):
        def decorator(fn: Callable):
            self._tools[definition.name] = (definition, fn, schema)
            return fn
        return decorator

    def get(self, name: str):
        return self._tools.get(name)

    def requires_approval(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool[0].requires_approval if tool else False

    def validate_all_schemas(self):
        """Called by CLAUDE.md's validation step."""
        for name, (definition, fn, schema) in self._tools.items():
            assert issubclass(schema, BaseModel), f"{name} missing a Pydantic schema"


registry = ToolRegistry()


async def execute_tool(tool_name: str, raw_arguments: dict) -> dict:
    entry = registry.get(tool_name)
    if not entry:
        return {"error": f"Unknown tool: {tool_name}"}

    definition, fn, schema = entry
    start = time.monotonic()
    try:
        validated_args = schema(**raw_arguments)
        result = await fn(validated_args)
        status = "success"
    except Exception as exc:
        result = {"error": str(exc)}
        status = "failure"
    latency = time.monotonic() - start

    logger.info("tool_call", tool=tool_name, status=status, latency_seconds=latency, args=raw_arguments)
    return result
```

## Example Tool — read-only, no approval needed
```python
# tools/search_knowledge_base.py
from pydantic import BaseModel
from app.tools.registry import registry, ToolDefinition
from app.rag.retrieve import retrieve


class SearchKBArgs(BaseModel):
    query: str
    top_k: int = 5


@registry.register(
    ToolDefinition(name="search_knowledge_base", description="Search the RAG index", requires_approval=False),
    schema=SearchKBArgs,
)
async def search_knowledge_base(args: SearchKBArgs) -> dict:
    results = retrieve(args.query, top_k=args.top_k)
    return {"results": results}
```

## Example Tool — mutates an external system, requires approval
```python
# tools/update_student_record.py
from pydantic import BaseModel
from app.tools.registry import registry, ToolDefinition


class UpdateRecordArgs(BaseModel):
    student_id: str
    field: str
    value: str


@registry.register(
    ToolDefinition(name="update_student_record", description="Write to the CRM", requires_approval=True),
    schema=UpdateRecordArgs,
)
async def update_student_record(args: UpdateRecordArgs) -> dict:
    # actual CRM API call here — only reached after the approval gate in harness/loop.py passes
    ...
```

## Sandboxing (timeouts + resource limits)
```python
# tools/sandbox.py
import asyncio


async def run_sandboxed(fn, *args, timeout_seconds: int = 10, **kwargs):
    try:
        return await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return {"error": f"Tool timed out after {timeout_seconds}s"}
```

## Best Practices
- Every tool's arguments are a Pydantic model — no raw dict passed straight to an external API
- `requires_approval` is set at registration time, not decided dynamically inside the tool — the gate must be able to check it before the tool runs at all
- Tools that call external APIs get a timeout; a hung external call should never hang the whole agent loop
- Log every call regardless of outcome — silent tool failures are one of the hardest bugs to diagnose after the fact
