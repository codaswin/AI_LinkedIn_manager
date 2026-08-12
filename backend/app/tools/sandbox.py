from __future__ import annotations

import asyncio
import time
import typing as t

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)


class ToolExecutionResult(BaseModel):
    tool_name: str
    status: t.Literal["success", "error", "timeout"]
    result: dict[str, t.Any] | None = None
    error: str | None = None
    latency_seconds: float


async def run_tool_sandboxed(
    tool_name: str,
    fn: t.Callable[[t.Any], t.Awaitable[dict[str, t.Any]]],
    validated_args: t.Any,
    timeout_seconds: int = 10,
) -> ToolExecutionResult:
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(fn(validated_args), timeout=timeout_seconds)
        latency = time.monotonic() - start
        logger.info("tool_executed", tool=tool_name, status="success", latency_seconds=latency)
        return ToolExecutionResult(
            tool_name=tool_name,
            status="success",
            result=result if isinstance(result, dict) else {"value": result},
            latency_seconds=latency,
        )
    except asyncio.TimeoutError:
        latency = time.monotonic() - start
        logger.error("tool_timeout", tool=tool_name, timeout_seconds=timeout_seconds, latency_seconds=latency)
        return ToolExecutionResult(
            tool_name=tool_name,
            status="timeout",
            error=f"Tool '{tool_name}' timed out after {timeout_seconds}s",
            latency_seconds=latency,
        )
    except Exception as exc:
        latency = time.monotonic() - start
        logger.exception("tool_exception", tool=tool_name, error=str(exc), latency_seconds=latency)
        return ToolExecutionResult(
            tool_name=tool_name,
            status="error",
            error=str(exc),
            latency_seconds=latency,
        )
