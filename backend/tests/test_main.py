from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from app.database import Base, configure_engine
from app.main import app, get_db
from app.safety.api_auth import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, authenticate_credentials, create_user

# Every model touched by an endpoint under test must be imported here
# explicitly, not relied upon transitively via some other test module having
# already been collected first — Base.metadata.create_all() only creates
# tables for classes that have actually been imported by the time it runs.
from app.models.agent_setting import AgentSetting  # noqa: F401
from app.models.approval_request import ApprovalRequestRecord
from app.models.feedback import FeedbackRecord  # noqa: F401
from app.models.learning_proposal import LearningProposalRecord
from app.models.platform_credential import PlatformCredentialRecord  # noqa: F401
from app.tools import registry as registry_module
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The `client` fixture builds an ASGITransport directly, which does not trigger
# FastAPI's lifespan() — so the tool registry (normally populated there) must be
# populated here explicitly, same pattern as test_tools.py/test_research.py.
registry_module._import_all_tools()


@pytest.fixture(autouse=True)
def _isolate_llm_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this file gets a deterministic (no live model) provider

    state by default, regardless of what's exported in whatever shell
    actually runs this suite. model_router._hosted_provider() auto-detects
    ANTHROPIC vs. OPENAI from whichever API key is present in the
    environment — an ambient real key left unmocked would silently route a
    workflow test onto a real, billed API call instead of the deterministic
    503 (or mocked-model) path the test actually intends to exercise.
    Individual tests that want the OpenAI/Anthropic path re-set the
    relevant var themselves.

    A full os.environ snapshot/restore, not just delenv of the known LLM
    vars: /credentials PUT writes straight to os.environ (by design — a
    saved key must take effect immediately, see
    memory/platform_credentials.py), which monkeypatch's own revert can't
    see or undo since it didn't make that change. Restoring the whole
    environment is the only way to guarantee nothing a credentials test
    saves (OPENAI_API_KEY, REDDIT_CLIENT_ID, ...) leaks into a later test.
    """
    snapshot = dict(os.environ)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    # Also stripped: a real (or merely present) COMPOSIO_API_KEY in whatever
    # .env this suite happens to run against would otherwise let an approval
    # test that reaches publish_post/schedule_post attempt a real network
    # call to Composio instead of hitting the deterministic "not configured"
    # path the test intends to exercise.
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.delenv("COMPOSIO_LINKEDIN_CONNECTED_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("COMPOSIO_X_CONNECTED_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    # Both provider clients cache their SDK instance at module level on first
    # use (see each client's _get_client()) — a real client built while a
    # real key happened to be present would otherwise survive, unaffected by
    # the monkeypatch.delenv above, into every later test in the process.
    from app.llmops import anthropic_client, openai_client
    from app.safety import secrets as secrets_module
    from app.tools import composio_client

    anthropic_client.reset_client_cache()
    openai_client.reset_client_cache()
    composio_client.reset_client_cache()
    secrets_module.reset_for_testing()
    yield
    os.environ.clear()
    os.environ.update(snapshot)
    secrets_module.reset_for_testing()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    configure_engine(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        await create_user(session, "test-admin", "test-password-strong", role="admin")
        _, session_token, csrf_token = await authenticate_credentials(
            session, "test-admin", "test-password-strong", "test-client"
        )

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={SESSION_COOKIE_NAME: session_token},
            headers={CSRF_HEADER_NAME: csrf_token},
        ) as ac:
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
            arguments={"content": "hello world"},
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
    response = await client.post("/approvals/appr-1/reject", json={"decided_by": "human:test", "reason": "not ready"})
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
# Activity
# ---------------------------------------------------------------------------


async def test_activity_idle_by_default(client: AsyncClient) -> None:
    response = await client.get("/activity")
    assert response.status_code == 200
    assert response.json() is None


async def test_activity_reports_current_state(client: AsyncClient) -> None:
    from app.activity import reset_for_testing, set_activity

    reset_for_testing()
    set_activity("research", "researching", detail="Searching Reddit", source="reddit")
    try:
        response = await client.get("/activity")
        body = response.json()
        assert body["agent"] == "research"
        assert body["source"] == "reddit"
    finally:
        reset_for_testing()


# ---------------------------------------------------------------------------
# Brand voice
# ---------------------------------------------------------------------------


async def test_create_and_list_brand_voice(client: AsyncClient) -> None:
    create_response = await client.post(
        "/brand-voice", json={"title": "Confident Founder Voice", "content": "Direct, short sentences."}
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["title"] == "Confident Founder Voice"

    list_response = await client.get("/brand-voice")
    assert list_response.status_code == 200
    assert [b["id"] for b in list_response.json()] == [created["id"]]


async def test_create_brand_voice_rejects_empty_title(client: AsyncClient) -> None:
    response = await client.post("/brand-voice", json={"title": "  ", "content": "some content"})
    assert response.status_code == 422


async def test_read_brand_voice_404s_for_unknown_id(client: AsyncClient) -> None:
    response = await client.get("/brand-voice/does-not-exist")
    assert response.status_code == 404


async def test_update_brand_voice_happy_path(client: AsyncClient) -> None:
    created = (await client.post("/brand-voice", json={"title": "Original", "content": "original"})).json()
    response = await client.put(f"/brand-voice/{created['id']}", json={"title": "Updated", "content": "updated"})
    assert response.status_code == 200
    assert response.json()["title"] == "Updated"


async def test_delete_brand_voice_happy_path(client: AsyncClient) -> None:
    created = (await client.post("/brand-voice", json={"title": "Temp", "content": "temp content"})).json()
    delete_response = await client.delete(f"/brand-voice/{created['id']}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True}

    get_response = await client.get(f"/brand-voice/{created['id']}")
    assert get_response.status_code == 404


async def test_delete_brand_voice_404s_for_unknown_id(client: AsyncClient) -> None:
    response = await client.delete("/brand-voice/does-not-exist")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Connections (platform credentials)
# ---------------------------------------------------------------------------


async def test_list_credentials_includes_every_known_platform(client: AsyncClient) -> None:
    response = await client.get("/credentials")
    assert response.status_code == 200
    ids = {p["id"] for p in response.json()}
    assert {"openai", "anthropic", "composio", "linkedin", "reddit", "github"} <= ids


async def test_save_credentials_happy_path(client: AsyncClient) -> None:
    response = await client.put("/credentials/openai", json={"values": {"api_key": "sk-abcd1234"}})
    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert body["fields"][0]["masked_preview"] == "••••1234"
    assert "sk-abcd1234" not in response.text


async def test_save_credentials_rejects_missing_field(client: AsyncClient) -> None:
    response = await client.put("/credentials/reddit", json={"values": {"client_id": "abc"}})
    assert response.status_code == 422


async def test_save_credentials_unknown_platform_422s(client: AsyncClient) -> None:
    response = await client.put("/credentials/not-a-real-platform", json={"values": {"x": "y"}})
    assert response.status_code == 422


async def test_delete_credentials_happy_path(client: AsyncClient) -> None:
    await client.put("/credentials/openai", json={"values": {"api_key": "sk-abcd1234"}})
    response = await client.delete("/credentials/openai")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    listed = (await client.get("/credentials")).json()
    assert next(p for p in listed if p["id"] == "openai")["connected"] is False


async def test_delete_credentials_when_nothing_saved(client: AsyncClient) -> None:
    response = await client.delete("/credentials/openai")
    assert response.status_code == 200
    assert response.json() == {"deleted": False}


# ---------------------------------------------------------------------------
# Workflow triggers
# ---------------------------------------------------------------------------


async def test_research_workflow_works_without_a_live_model(client: AsyncClient, monkeypatch) -> None:
    """The research workflow is synthesis-free — no ANTHROPIC_API_KEY needed.

    Sources are mocked here purely to keep this a fast, offline unit test,
    not because the endpoint itself requires it.
    """
    from app.agents import research_pipeline
    from app.agents.research_schema import ResearchResult

    async def fake_hackernews(query: str, limit: int) -> list[ResearchResult]:
        return [ResearchResult(source="hackernews", title="A real result", url="https://example.com/a")]

    monkeypatch.setitem(research_pipeline.ALL_SOURCES, "hackernews", fake_hackernews)

    response = await client.post("/workflows/research", json={"query": "AI agents", "sources": ["hackernews"]})
    assert response.status_code == 200
    body = response.json()
    assert body["result_count"] == 1
    assert body["results"][0]["source"] == "hackernews"


async def test_research_workflow_rejects_unknown_source(client: AsyncClient) -> None:
    response = await client.post("/workflows/research", json={"query": "AI agents", "sources": ["not-a-real-source"]})
    assert response.status_code == 422


async def test_content_workflow_503s_without_a_live_model(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from app.llmops import anthropic_client

    anthropic_client.reset_client_cache()

    response = await client.post("/workflows/content", json={"calendar_entries": ["agentic AI trends"]})
    assert response.status_code == 503
    assert "Anthropic" in response.json()["detail"]


async def test_engagement_workflow_503s_without_a_live_model(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from app.llmops import anthropic_client

    anthropic_client.reset_client_cache()

    response = await client.post("/workflows/engagement", json={"notification_type": "comment", "text": "Great post!"})
    assert response.status_code == 503


async def test_analytics_workflow_happy_path_with_mocked_model(client: AsyncClient, monkeypatch) -> None:
    """Proves the trigger -> agent -> response wiring works end to end when

    a model IS available, not just that it fails gracefully when it isn't
    (covered by the 503 tests above).
    """
    import app.llmops.model_router as model_router_module
    from app.llmops.model_router import RouteAndCallResponse

    async def fake_route_and_call(*, state, config):
        return RouteAndCallResponse(text='{"flagged_posts": []}', confidence=0.9, goal_achieved=True)

    monkeypatch.setattr(model_router_module, "route_and_call", fake_route_and_call)

    response = await client.post("/workflows/analytics", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["flagged_posts"] == []
    assert "period_start" in body


# ---------------------------------------------------------------------------
# App lifecycle — exercises the test schema opt-in and scheduler
# ---------------------------------------------------------------------------


def test_app_boots_and_shuts_down_cleanly(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    configure_engine(engine)
    monkeypatch.setenv("AUTO_CREATE_SCHEMA", "true")

    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        response = test_client.get("/health")
        assert response.status_code == 200

    from app.learning.scheduler import get_scheduler

    assert get_scheduler() is None  # stopped cleanly on shutdown


async def test_protected_routes_require_authentication() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        assert (await anonymous.get("/health")).status_code == 200
        response = await anonymous.get("/credentials")
        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required"


async def test_mutations_require_csrf(client: AsyncClient) -> None:
    csrf = client.headers.pop(CSRF_HEADER_NAME)
    try:
        response = await client.put("/settings/research_agent.poll_interval", json={"value": "hourly"})
        assert response.status_code == 403
        assert response.json()["detail"] == "Missing or invalid CSRF token"
    finally:
        client.headers[CSRF_HEADER_NAME] = csrf


async def test_browser_cannot_forge_setting_identity(client: AsyncClient) -> None:
    response = await client.put(
        "/settings/research_agent.poll_interval",
        json={"value": "hourly", "updated_by": "forged-browser-user"},
    )
    assert response.status_code == 200
    assert response.json()["updated_by"] == "test-admin"


async def test_browser_cannot_forge_approval_identity(client: AsyncClient) -> None:
    await _seed_approval(client)
    response = await client.post("/approvals/appr-1/approve", json={"decided_by": "forged-browser-user"})
    assert response.status_code == 200
    override = app.dependency_overrides[get_db]
    async for db in override():
        record = await db.get(ApprovalRequestRecord, "appr-1")
        assert record is not None
        assert record.decided_by == "test-admin"
        assert record.status == "failed"
        assert record.attempt_count == 1


async def test_login_sets_secure_httponly_strict_cookie(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    response = await client.post("/auth/login", json={"username": "test-admin", "password": "test-password-strong"})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie


async def test_viewer_role_cannot_mutate_or_read_credentials(client: AsyncClient) -> None:
    override = app.dependency_overrides[get_db]
    async for db in override():
        await create_user(db, "test-viewer", "viewer-password-strong", role="viewer")
        _, token, csrf = await authenticate_credentials(db, "test-viewer", "viewer-password-strong", "test-client")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE_NAME: token},
        headers={CSRF_HEADER_NAME: csrf},
    ) as viewer:
        assert (await viewer.get("/approvals")).status_code == 200
        assert (await viewer.get("/credentials")).status_code == 403
        response = await viewer.put("/settings/research_agent.poll_interval", json={"value": "hourly"})
        assert response.status_code == 403
        assert response.json()["detail"] == "operator role required"


async def test_login_is_rate_limited_in_shared_state(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_LOGIN_MAX_ATTEMPTS", "2")
    for _ in range(2):
        response = await client.post("/auth/login", json={"username": "test-admin", "password": "wrong-password"})
        assert response.status_code == 401
    response = await client.post("/auth/login", json={"username": "test-admin", "password": "test-password-strong"})
    assert response.status_code == 429


async def test_bootstrap_password_rotation_revokes_existing_sessions(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.safety.api_auth import ensure_bootstrap_admin

    monkeypatch.setenv("DASHBOARD_ADMIN_USERNAME", "test-admin")
    monkeypatch.setenv("DASHBOARD_ADMIN_PASSWORD", "rotated-password-strong")
    override = app.dependency_overrides[get_db]
    async for db in override():
        await ensure_bootstrap_admin(db)
    assert (await client.get("/auth/me")).status_code == 401
    response = await client.post("/auth/login", json={"username": "test-admin", "password": "rotated-password-strong"})
    assert response.status_code == 200
