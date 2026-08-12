from __future__ import annotations

import typing as t

from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel

PeriodLiteral = t.Literal["daily", "weekly", "monthly", "quarterly", "yearly"]


class GenerateAnalyticsReportArgs(BaseModel):
    period: PeriodLiteral = "weekly"


@registry.register(
    ToolDefinition(
        name="generate_analytics_report",
        description="Summarize stored engagement data for a period (no external call)",
        requires_approval=False,
    ),
    schema=GenerateAnalyticsReportArgs,
)
async def execute(args: GenerateAnalyticsReportArgs) -> dict[str, t.Any]:
    # TODO(memory-agent, analytics-agent): pull real engagement stats from episodic memory;
    # returns a stub shape so downstream agents can integrate against a stable contract now.
    return {
        "period": args.period,
        "impressions": 0,
        "engagement_rate": 0.0,
        "follower_delta": 0,
        "top_posts": [],
    }
