"""FastAPI serving layer — an HTTP surface over infrastructure that already

exists and is fully tested elsewhere (memory.settings, safety.approval_gate,
learning.proposal_review, llmops.cost_tracker). This file adds no new
business logic of its own; every endpoint is a thin wrapper.

This is what the PRP's "exposed via a FastAPI endpoint for the future
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
from typing import Any

import structlog
from app.database import get_session, init_models
from app.learning import proposal_review
from app.learning.proposal_review import (
    ProposalAlreadyDecidedError,
    ProposalNotFoundError,
)
from app.learning.scheduler import start_scheduler, stop_scheduler
from app.memory.settings import get_setting, set_setting
from app.safety import approval_gate
from app.safety.approval_gate import (
    ApprovalRequestAlreadyDecidedError,
    ApprovalRequestNotFoundError,
    SystemPausedError,
)
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_models()
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


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


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
