from __future__ import annotations

import argparse
import sys
import typing as t
from dataclasses import dataclass

import structlog
from app.tools.sandbox import run_tool_sandboxed
from pydantic import BaseModel, ValidationError

logger = structlog.get_logger(__name__)


class ToolDefinition(BaseModel):
    name: str
    description: str
    requires_approval: bool
    timeout_seconds: int = 10


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    schema: type[BaseModel]
    fn: t.Callable[[t.Any], t.Awaitable[dict[str, t.Any]]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, definition: ToolDefinition, schema: type[BaseModel]) -> t.Callable:
        if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            raise TypeError(f"{definition.name}: schema must be a Pydantic BaseModel subclass")
        if not isinstance(definition.requires_approval, bool):
            raise TypeError(f"{definition.name}: requires_approval must be an explicit bool")

        def decorator(fn: t.Callable[[t.Any], t.Awaitable[dict[str, t.Any]]]):
            if definition.name in self._tools:
                raise ValueError(f"Tool '{definition.name}' is already registered")
            self._tools[definition.name] = RegisteredTool(definition=definition, schema=schema, fn=fn)
            return fn

        return decorator

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def all(self) -> dict[str, RegisteredTool]:
        return dict(self._tools)

    def requires_approval(self, name: str) -> bool:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
        return tool.definition.requires_approval

    def validate_all_schemas(self) -> list[str]:
        errors: list[str] = []
        for name, reg in self._tools.items():
            if not (isinstance(reg.schema, type) and issubclass(reg.schema, BaseModel)):
                errors.append(f"{name}: missing a Pydantic input schema")
                continue
            if not isinstance(reg.definition.requires_approval, bool):
                errors.append(f"{name}: requires_approval is not an explicit bool")

            if name == "delete_post":
                errors.extend(_validate_delete_post_schema(reg.schema))

        return errors


def _validate_delete_post_schema(schema: type[BaseModel]) -> list[str]:
    errors: list[str] = []
    required_fields = ("post_content", "published_at", "engagement_stats")
    model_fields = schema.model_fields
    for field_name in required_fields:
        field_info = model_fields.get(field_name)
        if field_info is None:
            errors.append(f"delete_post: schema is missing required field '{field_name}'")
        elif not field_info.is_required():
            errors.append(f"delete_post: field '{field_name}' must be required (non-optional), not have a default")
    return errors


registry = ToolRegistry()


class ApprovalRequiredError(RuntimeError):
    """Raised when a requires_approval tool is invoked without an explicit approval grant.

    project invariant #3 has no bypass — this is a defense-in-depth check at the
    execution boundary, additive to (not a replacement for) the dedicated approval-gate the
    safety-agent builds on top of this registry.
    """


async def execute_tool(tool_name: str, raw_arguments: dict[str, t.Any], approved: bool = False) -> dict[str, t.Any]:
    entry = registry.get(tool_name)
    if entry is None:
        return {"status": "error", "error": f"Unknown tool: {tool_name}"}

    if entry.definition.requires_approval and not approved:
        logger.warning("tool_blocked_unapproved", tool=tool_name)
        return {
            "status": "blocked",
            "error": f"Tool '{tool_name}' requires human approval and was not marked approved.",
        }

    try:
        validated_args = entry.schema(**raw_arguments)
    except (ValidationError, TypeError) as exc:
        logger.error("tool_args_invalid", tool=tool_name, error=str(exc))
        return {"status": "error", "error": f"Invalid arguments for '{tool_name}': {exc}"}

    result = await run_tool_sandboxed(
        tool_name,
        entry.fn,
        validated_args,
        timeout_seconds=entry.definition.timeout_seconds,
    )
    return result.model_dump()


def _import_all_tools() -> None:
    from app.tools import (  # noqa: F401
        delete_post,
        draft_post,
        generate_analytics_report,
        get_linkedin_notifications,
        like_post,
        publish_post,
        reply_to_comment,
        reply_to_dm,
        save_research_note,
        schedule_post,
        search_github,
        search_hackernews,
        search_knowledge_base,
        search_producthunt,
        search_reddit,
        search_rss,
        search_web,
        search_x_posts,
        send_connection_request,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.tools.registry")
    parser.add_argument(
        "--validate-all-schemas",
        action="store_true",
        help="Assert every registered tool has a Pydantic schema and an explicit requires_approval bool.",
    )
    args = parser.parse_args(argv)

    _import_all_tools()

    if not args.validate_all_schemas:
        parser.print_help()
        return 0

    tools = registry.all()
    if not tools:
        print("FAIL: no tools registered", file=sys.stderr)
        return 1

    errors = registry.validate_all_schemas()
    if errors:
        print(f"FAIL: {len(errors)} schema validation error(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    approval_count = sum(1 for reg in tools.values() if reg.definition.requires_approval)
    print(
        f"OK: {len(tools)} tools validated — all have Pydantic schemas and explicit "
        f"requires_approval flags ({approval_count} require approval)."
    )
    return 0


if __name__ == "__main__":
    # Running as `python -m app.tools.registry` loads this file twice under different module
    # names (__main__ here, plus a separate `app.tools.registry` import pulled in by each tool
    # submodule) unless we force a single canonical import — otherwise the registry populated
    # by `_import_all_tools()` and the one this block inspects would be two different objects.
    from app.tools.registry import main as _canonical_main

    sys.exit(_canonical_main())
