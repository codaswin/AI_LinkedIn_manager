"""FastAPI serving layer — an HTTP surface over infrastructure that already

exists and is fully tested elsewhere (memory.settings, safety.approval_gate,
learning.proposal_review, llmops.cost_tracker). This file adds no new
business logic of its own; every endpoint is a thin wrapper.

This is what the product spec's "exposed via a FastAPI endpoint for the future
dashboard UI" (memory-agent, Phase 1) and the Post-MVP "dashboard UI control"
roadmap item build against — the API exists now, a UI can point at it later
without this file changing.

Every `= Depends(get_db)` default below is ruff bugbear rule B008's
canonical false positive: FastAPI's own dependency-injection idiom, not the
mutable-default-argument bug that rule exists to catch. Silenced once here
rather than per line.
"""

# ruff: noqa: B008

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any, Literal

import structlog
from app import activity as activity_module
from app.database import get_session, get_session_factory, init_models
from app.learning import proposal_review
from app.learning.proposal_review import (
    ProposalAlreadyDecidedError,
    ProposalNotFoundError,
)
from app.learning.scheduler import start_scheduler, stop_scheduler
from app.llmops.anthropic_client import AnthropicConfigError
from app.llmops.hermes_client import HermesCallError
from app.llmops.openai_client import OpenAIConfigError
from app.memory import brand_voice as brand_voice_memory
from app.memory import platform_credentials
from app.memory.settings import get_setting, set_setting
from app.safety import approval_gate
from app.safety.approval_gate import (
    ApprovalRequestAlreadyDecidedError,
    ApprovalRequestNotFoundError,
    SystemPausedError,
)
from app.safety.api_auth import is_public_path, require_dashboard_api_key
from app.safety.secrets import CredentialEncryptionError
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Every tools/*.py module registers itself with tools.registry as a side
    # effect of being imported — nothing imports them at process startup
    # otherwise, so every execute_tool() call (every workflow trigger, every
    # approval execution) would silently fail with "Unknown tool" without
    # this. Every other entrypoint (pytest fixtures, safety.audit's CLI)
    # already does this explicitly; the live server needs the same call.
    from app.tools.registry import _import_all_tools

    _import_all_tools()
    await init_models()
    # Replay anything saved through the Connections page back into
    # os.environ — the DB row is the durable copy, but every credential
    # consumer (anthropic_client, search_reddit, ...) still just reads
    # os.environ, so a restart needs this to not lose what was configured.
    async with get_session_factory()() as session:
        await platform_credentials.load_saved_credentials_into_env(session)
    start_scheduler()
    logger.info("app_startup_complete")
    yield
    stop_scheduler()
    logger.info("app_shutdown_complete")


app = FastAPI(
    title="AI LinkedIn Manager",
    description="Runtime API for agent settings, the human-approval queue, and the self-learning review queue.",
    version="0.1.0",
    lifespan=lifespan,
)

# The dashboard frontend (frontend/, a separate Vite dev server / static
# build) runs on a different origin than this API, so it needs CORS
# explicitly enabled. Defaults cover Vite's dev-server ports; override via
# CORS_ALLOWED_ORIGINS (comma-separated) for a real deployment's actual
# frontend origin — never widen this to "*" once credentials/cookies are
# in play.
_DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
_cors_origins = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", _DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _dashboard_api_key_guard(request: Request, call_next):
    # Disabled unless DASHBOARD_API_KEY is set. When enabled, every non-public
    # endpoint requires X-Dashboard-API-Key so approval and credential actions
    # are not exposed by accident in a deployed environment.
    if not is_public_path(request.url.path):
        require_dashboard_api_key(request)
    return await call_next(request)


# Any endpoint that can trigger a real model call (the /workflows/* triggers,
# /learning/reflect) fails loudly rather than a bare 500 when no live model
# is configured — 503 "unavailable" is the honest status for "this needs a
# dependency that isn't set up," not a server bug.
@app.exception_handler(AnthropicConfigError)
async def _anthropic_config_error_handler(request: Request, exc: AnthropicConfigError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": f"Anthropic client not available: {exc}"})


@app.exception_handler(HermesCallError)
async def _hermes_call_error_handler(request: Request, exc: HermesCallError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": f"Hermes/vLLM worker not available: {exc}"})


@app.exception_handler(OpenAIConfigError)
async def _openai_config_error_handler(request: Request, exc: OpenAIConfigError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": f"OpenAI client not available: {exc}"})


@app.exception_handler(CredentialEncryptionError)
async def _credential_encryption_error_handler(request: Request, exc: CredentialEncryptionError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/activity")
async def read_activity() -> dict[str, Any] | None:
    """Polled by the dashboard's ActivityBanner every ~1.2s — None means idle."""
    return activity_module.get_activity()


# ---------------------------------------------------------------------------
# Agent settings — e.g. research_agent.poll_interval, editable without a redeploy
# ---------------------------------------------------------------------------


class SettingUpdate(BaseModel):
    value: str
    updated_by: str


@app.get("/settings/{key}")
async def read_setting(key: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    value = await get_setting(db, key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"No value or default registered for setting {key!r}")
    return {"key": key, "value": value}


@app.put("/settings/{key}")
async def update_setting(key: str, body: SettingUpdate, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    record = await set_setting(db, key, body.value, updated_by=body.updated_by)
    return {"key": record.key, "value": record.value, "updated_by": record.updated_by, "updated_at": record.updated_at}


# ---------------------------------------------------------------------------
# Brand voice — titled profiles, stored in the Content Writer/Engagement
# Agents' semantic memory (RAG "brand_voice" source) as well as listed here
# ---------------------------------------------------------------------------


def _brand_voice_dict(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "title": record.title,
        "content": record.content,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


class BrandVoiceCreate(BaseModel):
    title: str
    content: str


@app.get("/brand-voice")
async def list_brand_voice(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    records = await brand_voice_memory.list_brand_voices(db)
    return [_brand_voice_dict(r) for r in records]


@app.post("/brand-voice")
async def create_brand_voice(body: BrandVoiceCreate, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        record = await brand_voice_memory.create_brand_voice(db, title=body.title, content=body.content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _brand_voice_dict(record)


@app.get("/brand-voice/{brand_voice_id}")
async def read_brand_voice(brand_voice_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    record = await brand_voice_memory.get_brand_voice(db, brand_voice_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No brand voice with id {brand_voice_id!r}")
    return _brand_voice_dict(record)


@app.put("/brand-voice/{brand_voice_id}")
async def update_brand_voice(
    brand_voice_id: str, body: BrandVoiceCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        record = await brand_voice_memory.update_brand_voice(
            db, brand_voice_id, title=body.title, content=body.content
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"No brand voice with id {brand_voice_id!r}")
    return _brand_voice_dict(record)


@app.delete("/brand-voice/{brand_voice_id}")
async def delete_brand_voice(brand_voice_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    deleted = await brand_voice_memory.delete_brand_voice(db, brand_voice_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No brand voice with id {brand_voice_id!r}")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Connections — where a human pastes API keys/tokens/OAuth IDs for every
# platform this system talks to, instead of hand-editing .env. See
# memory/platform_credentials.py's PLATFORM_SCHEMA for what each platform
# needs and why; this file only wraps it as HTTP.
# ---------------------------------------------------------------------------


@app.get("/credentials")
async def list_credentials(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    return await platform_credentials.list_platform_status(db)


class CredentialSaveBody(BaseModel):
    values: dict[str, str]


@app.put("/credentials/{platform_id}")
async def save_credentials(
    platform_id: str, body: CredentialSaveBody, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        await platform_credentials.save_platform_credentials(db, platform_id, body.values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    statuses = await platform_credentials.list_platform_status(db)
    return next(s for s in statuses if s["id"] == platform_id)


@app.delete("/credentials/{platform_id}")
async def clear_credentials(platform_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    try:
        deleted = await platform_credentials.delete_platform_credentials(db, platform_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# Approval queue
# ---------------------------------------------------------------------------


class DecisionBody(BaseModel):
    decided_by: str
    reason: str | None = None


@app.get("/approvals")
async def list_approvals(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    pending = await approval_gate.list_pending(db)
    return [p.model_dump(mode="json") for p in pending]


@app.post("/approvals/{approval_id}/approve")
async def approve_approval(approval_id: str, body: DecisionBody, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await approval_gate.approve(db, approval_id, decided_by=body.decided_by)
    except SystemPausedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except ApprovalRequestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalRequestAlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/approvals/{approval_id}/reject")
async def reject_approval(approval_id: str, body: DecisionBody, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        record = await approval_gate.reject(db, approval_id, decided_by=body.decided_by, reason=body.reason)
    except ApprovalRequestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalRequestAlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return record.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Manual workflow triggers — each wraps an existing agent entrypoint with
# app.activity's reporting (via the agent modules themselves) so the
# dashboard's ActivityBanner shows real-time progress. None of these add new
# agent logic — they only wire an HTTP request to the same functions the
# scheduler/other call sites already use.
# ---------------------------------------------------------------------------


class ResearchWorkflowBody(BaseModel):
    query: str
    sources: list[str] | None = None
    limit_per_source: int = 10


@app.post("/workflows/research")
async def trigger_research_workflow(body: ResearchWorkflowBody) -> dict[str, Any]:
    """Synthesis-free — no live model required, so this works even without

    ANTHROPIC_API_KEY configured (Hacker News/RSS/DuckDuckGo need no key at
    all; Reddit/GitHub/Product Hunt degrade gracefully if uncredentialed).
    """
    from app.agents.research_pipeline import research

    try:
        results = await research(body.query, sources=body.sources, limit_per_source=body.limit_per_source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"query": body.query, "result_count": len(results), "results": results}


class ContentWorkflowBody(BaseModel):
    calendar_entries: list[str] = []
    recent_post_topics: list[str] = []


@app.post("/workflows/content")
async def trigger_content_workflow(body: ContentWorkflowBody, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Content Strategist -> Content Writer, chained: the brief the

    Strategist produces is fed straight into the Writer, landing either in
    the approval queue or flagged needs_human_rewrite — same outcome as the
    two agents running back-to-back in production.
    """
    from app.agents.content_strategist import build_post_brief
    from app.agents.content_writer import write_post
    from app.llmops.model_router import route_and_call

    brief = await build_post_brief(body.recent_post_topics, body.calendar_entries, route_and_call)
    result = await write_post(brief.model_dump(mode="json"), route_and_call, db=db)
    return {"brief": brief.model_dump(mode="json"), **result}


class AnalyticsWorkflowBody(BaseModel):
    period_start: date | None = None
    period_end: date | None = None


@app.post("/workflows/analytics")
async def trigger_analytics_workflow(body: AnalyticsWorkflowBody, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from app.agents.analytics import generate_weekly_digest
    from app.llmops.model_router import route_and_call

    period_end = body.period_end or date.today()
    period_start = body.period_start or (period_end - timedelta(days=7))
    digest = await generate_weekly_digest(db, route_and_call, period_start, period_end)
    return digest.model_dump(mode="json")


class EngagementWorkflowBody(BaseModel):
    notification_type: Literal["comment", "dm", "connection_request"]
    text: str
    notification_id: str = "manual-trigger"


@app.post("/workflows/engagement")
async def trigger_engagement_workflow(body: EngagementWorkflowBody, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from app.agents.engagement import handle_notification
    from app.llmops.model_router import route_and_call

    notification = {"id": body.notification_id, "type": body.notification_type, "text": body.text}
    return await handle_notification(notification, route_and_call, db=db)


# ---------------------------------------------------------------------------
# Self-learning review queue
# ---------------------------------------------------------------------------


@app.get("/learning/proposals")
async def list_learning_proposals(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    pending = await proposal_review.list_pending(db)
    return [p.model_dump(mode="json") for p in pending]


@app.post("/learning/proposals/{proposal_id}/approve")
async def approve_learning_proposal(
    proposal_id: str, body: DecisionBody, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        record = await proposal_review.approve_proposal(db, proposal_id, decided_by=body.decided_by)
    except ProposalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProposalAlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return record.model_dump(mode="json")


@app.post("/learning/proposals/{proposal_id}/reject")
async def reject_learning_proposal(
    proposal_id: str, body: DecisionBody, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        record = await proposal_review.reject_proposal(db, proposal_id, decided_by=body.decided_by, reason=body.reason)
    except ProposalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProposalAlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return record.model_dump(mode="json")


class ReflectBody(BaseModel):
    days: int = 7


@app.post("/learning/reflect")
async def trigger_reflection(body: ReflectBody, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """On-demand reflection run — the scheduler (learning/scheduler.py) fires

    this same job weekly by default; this endpoint exists for manual/testing
    triggers between scheduled runs.
    """
    from app.learning.reflection_job import run_reflection
    from app.llmops.model_router import route_and_call

    result = await run_reflection(db, route_and_call, days=body.days)
    return {**result, "proposals": [p.model_dump(mode="json") for p in result["proposals"]]}


# ---------------------------------------------------------------------------
# Cost observability
# ---------------------------------------------------------------------------


@app.get("/cost")
async def cost_summary() -> dict[str, float]:
    from app.llmops.cost_tracker import get_cost_summary

    return get_cost_summary()
