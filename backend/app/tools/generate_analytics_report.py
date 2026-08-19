from __future__ import annotations

import typing as t
from datetime import date, datetime, time, timedelta, timezone

from app.database import get_session_factory
from app.models.episode import PostEpisode
from app.tenancy.context import get_current_user_id
from app.tools.registry import ToolDefinition, registry
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

PeriodLiteral = t.Literal["daily", "weekly", "monthly", "quarterly", "yearly"]
_PERIOD_DAYS: dict[str, int] = {"daily": 1, "weekly": 7, "monthly": 30, "quarterly": 90, "yearly": 365}


class GenerateAnalyticsReportArgs(BaseModel):
    period: PeriodLiteral = "weekly"
    period_start: date | None = None
    period_end: date | None = None


def _date_bounds(args: GenerateAnalyticsReportArgs) -> tuple[date, date]:
    end = args.period_end or datetime.now(timezone.utc).date()
    start = args.period_start or (end - timedelta(days=_PERIOD_DAYS[args.period] - 1))
    if start > end:
        raise ValueError("period_start must be on or before period_end")
    return start, end


async def build_report(db: AsyncSession, args: GenerateAnalyticsReportArgs) -> dict[str, t.Any]:
    start, end = _date_bounds(args)
    start_at = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_at = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    result = await db.execute(
        select(PostEpisode)
        .where(
            PostEpisode.published_at >= start_at,
            PostEpisode.published_at < end_at,
            PostEpisode.entity_id == get_current_user_id(),
        )
        .order_by(PostEpisode.published_at.desc())
    )
    posts = list(result.scalars())

    impressions = sum(post.impressions for post in posts)
    engagements = sum(post.likes + post.comments + post.shares for post in posts)
    engagement_rate = engagements / impressions if impressions else 0.0
    follower_delta = sum(post.follower_delta for post in posts)

    post_rows = [
        {
            "post_id": post.post_id,
            "content": post.content,
            "published_at": post.published_at.isoformat(),
            "impressions": post.impressions,
            "likes": post.likes,
            "comments": post.comments,
            "shares": post.shares,
            "follower_delta": post.follower_delta,
            "engagement_rate": (
                (post.likes + post.comments + post.shares) / post.impressions if post.impressions else 0.0
            ),
        }
        for post in posts
    ]
    top_posts = sorted(
        post_rows,
        key=lambda post: (post["engagement_rate"], post["impressions"]),
        reverse=True,
    )[:10]
    return {
        "period": args.period,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "post_count": len(posts),
        "impressions": impressions,
        "engagement_rate": engagement_rate,
        "follower_delta": follower_delta,
        "top_posts": top_posts,
        "posts": post_rows,
    }


@registry.register(
    ToolDefinition(
        name="generate_analytics_report",
        description="Aggregate stored LinkedIn engagement data for a date range",
        requires_approval=False,
    ),
    schema=GenerateAnalyticsReportArgs,
)
async def execute(args: GenerateAnalyticsReportArgs) -> dict[str, t.Any]:
    async with get_session_factory()() as db:
        return await build_report(db, args)
