from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from app.database import Base, configure_engine
from app.main import app, get_db

# Every model touched by an endpoint under test must be imported here
# explicitly, not relied upon transitively via some other test module having
# already been collected first — Base.metadata.create_all() only creates
# tables for classes that have actually been imported by the time it runs.
from app.models.agent_setting import AgentSetting  # noqa: F401
from app.models.approval_request import ApprovalRequestRecord
from app.models.feedback import FeedbackRecord  # noqa: F401
from app.models.learning_proposal import LearningProposalRecord
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    configure_engine(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


async def test_read_setting_returns_default_when_unset(client: AsyncClient) -> None:
    response = await client.get("/settings/research_agent.poll_interval")
    assert response.status_code == 200
    assert response.json() == {"key": "research_agent.poll_interval", "value": "daily"}


async def test_read_unknown_setting_404s(client: AsyncClient) -> None:
    response = await client.get("/settings/not_a_real_setting")
    assert response.status_code == 404


async def test_update_setting_then_read_reflects_it(client: AsyncClient) -> None:
    put_response = await client.put(
        "/settings/research_agent.poll_interval",
        json={"value": "hourly", "updated_by": "dashboard_ui:test"},
    )
    assert put_response.status_code == 200
    assert put_response.json()["value"] == "hourly"

    get_response = await client.get("/settings/research_agent.poll_interval")
    assert get_response.json()["value"] == "hourly"


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


async def _seed_approval(client: AsyncClient) -> dict[str, Any]:
    # Bypasses the HTTP layer for setup (there's no POST /approvals endpoint —
    # submission always originates from an agent, never a raw HTTP call) by
    # writing directly through the same session the override provides.
    from app.main import get_db as get_db_dep

    override = app.dependency_overrides[get_db_dep]
    async for db in override():
        record = ApprovalRequestRecord(
            id="appr-1",
            tool_name="publish_post",
            arguments={"post_content": "hello world", "topic": "test"},
            requested_by_agent="content_writer",
            reason="test seed",
            confidence=0.9,
            status="pending",
        )
        db.add(record)
        await db.commit()
    return {"id": "appr-1"}


async def test_list_approvals_returns_pending(client: AsyncClient) -> None:
    await _seed_approval(client)
    response = await client.get("/approvals")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "appr-1"
    assert body[0]["status"] == "pending"


async def test_approve_nonexistent_approval_404s(client: AsyncClient) -> None:
    response = await client.post("/approvals/does-not-exist/approve", json={"decided_by": "human:test"})
    assert response.status_code == 404


async def test_approve_approval_happy_path(client: AsyncClient) -> None:
    await _seed_approval(client)
    response = await client.post("/approvals/appr-1/approve", json={"decided_by": "human:test"})
    assert response.status_code == 200
    # The gated tool itself (publish_post -> Composio) has no credentials in
    # this test environment, so it reports a sandboxed tool-level error —
    # what matters here is the approval endpoint itself returned 200 and
    # actually invoked execute_tool(..., approved=True), not that Composio
    # is configured.
    assert response.json()["status"] == "error"


async def test_approve_already_decided_approval_409s(client: AsyncClient) -> None:
    await _seed_approval(client)
    await client.post("/approvals/appr-1/approve", json={"decided_by": "human:test"})
    response = await client.post("/approvals/appr-1/approve", json={"decided_by": "human:test"})
    assert response.status_code == 409


async def test_approve_approval_while_system_paused_423s(client: AsyncClient) -> None:
    from app.safety.kill_switch import pause_system, reset_for_testing

    await _seed_approval(client)
    pause_system(reason="test", paused_by="human:test")
    try:
        response = await client.post("/approvals/appr-1/approve", json={"decided_by": "human:test"})
        assert response.status_code == 423
    finally:
        reset_for_testing()


async def test_reject_approval_happy_path(client: AsyncClient) -> None:
    await _seed_approval(client)
    response = await client.post(
        "/approvals/appr-1/reject", json={"decided_by": "human:test", "reason": "not ready"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert "not ready" in body["reason"]


async def test_reject_already_decided_approval_409s(client: AsyncClient) -> None:
    await _seed_approval(client)
    await client.post("/approvals/appr-1/reject", json={"decided_by": "human:test"})
    response = await client.post("/approvals/appr-1/reject", json={"decided_by": "human:test"})
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Learning proposals
# ---------------------------------------------------------------------------


async def _seed_proposal(client: AsyncClient, change_type: str = "system_prompt") -> None:
    from app.main import get_db as get_db_dep

    override = app.dependency_overrides[get_db_dep]
    async for db in override():
        record = LearningProposalRecord(
            id="prop-1",
            pattern="drafts sound salesy",
            change_type=change_type,
            proposed_change="add tone guidance",
            confidence=0.9,
            status="pending",
        )
        db.add(record)
        await db.commit()


async def test_list_learning_proposals_returns_pending(client: AsyncClient) -> None:
    await _seed_proposal(client)
    response = await client.get("/learning/proposals")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "prop-1"


async def test_approve_learning_proposal_happy_path(client: AsyncClient) -> None:
    await _seed_proposal(client)
    response = await client.post("/learning/proposals/prop-1/approve", json={"decided_by": "human:test"})
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


async def test_reject_learning_proposal_happy_path(client: AsyncClient) -> None:
    await _seed_proposal(client)
    response = await client.post(
        "/learning/proposals/prop-1/reject", json={"decided_by": "human:test", "reason": "not convincing"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert "not convincing" in body["proposed_change"]


async def test_reject_nonexistent_learning_proposal_404s(client: AsyncClient) -> None:
    response = await client.post("/learning/proposals/does-not-exist/reject", json={"decided_by": "human:test"})
    assert response.status_code == 404


async def test_trigger_reflection_with_insufficient_feedback_still_200s(client: AsyncClient) -> None:
    response = await client.post("/learning/reflect", json={"days": 7})
    assert response.status_code == 200
    body = response.json()
    assert body["ran"] is False
    assert body["reason"] == "insufficient_feedback"


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


async def test_cost_summary(client: AsyncClient) -> None:
    response = await client.get("/cost")
    assert response.status_code == 200
    body = response.json()
    assert "today_usd" in body
    assert "budget_usd" in body


# ---------------------------------------------------------------------------
# App lifecycle — exercises the real lifespan (init_models + scheduler)
# ---------------------------------------------------------------------------


def test_app_boots_and_shuts_down_cleanly() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    configure_engine(engine)

    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        response = test_client.get("/health")
        assert response.status_code == 200

    from app.learning.scheduler import get_scheduler

    assert get_scheduler() is None  # stopped cleanly on shutdown
