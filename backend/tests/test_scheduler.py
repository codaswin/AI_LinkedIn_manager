from __future__ import annotations

import uuid

import pytest
from app.database import Base, configure_engine
from app.learning import scheduler
from app.models.approval_request import ApprovalRequestRecord  # noqa: F401
from app.models.auth import DashboardUserRecord
from app.models.feedback import FeedbackRecord  # noqa: F401
from app.models.learning_proposal import LearningProposalRecord  # noqa: F401
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def _mark_connected(db_session, user_id: str, platform_ids: set[str]) -> None:
    """Insert placeholder PlatformCredentialRecord rows so

    platform_credentials.list_platform_status reports these platforms as
    connected for user_id — engagement_job's pre-check (see
    app.learning.scheduler._is_connected) needs this to actually run a
    user through rather than skipping them. The encrypted_value content is
    never decrypted by list_platform_status, so a placeholder is enough.
    """
    from app.memory.platform_credentials import PLATFORM_SCHEMA, _record_id
    from app.models.platform_credential import PlatformCredentialRecord

    for platform in PLATFORM_SCHEMA:
        if platform.id not in platform_ids:
            continue
        for field in platform.fields:
            db_session.add(
                PlatformCredentialRecord(
                    id=_record_id(user_id, platform.id, field.name),
                    user_id=user_id,
                    platform_id=platform.id,
                    field_name=field.name,
                    encrypted_value="placeholder",
                    masked_preview="••••test",
                )
            )
    await db_session.commit()


async def _add_active_user(db_session, username: str = "scheduler-test-user") -> str:
    """Every fan-out job (see plans/peaceful-scribbling-tiger.md Stage 3)

    iterates active dashboard users first — a job body under test that's
    supposed to actually run needs at least one real row here, or the loop
    has nothing to iterate and silently does nothing.
    """
    user_id = str(uuid.uuid4())
    db_session.add(
        DashboardUserRecord(
            id=user_id, username=username, password_hash="unused-in-these-tests", role="admin", active=True
        )
    )
    await db_session.commit()
    return user_id


@pytest.fixture(autouse=True)
async def _reset_scheduler():
    # AsyncIOScheduler binds to whatever event loop is running when start()
    # is called, so setup/teardown must run in the SAME (function-scoped)
    # loop as the test itself — hence an async fixture, not a plain sync one.
    if scheduler.get_scheduler() is not None:
        scheduler.stop_scheduler()
    yield
    if scheduler.get_scheduler() is not None:
        scheduler.stop_scheduler()


async def test_start_scheduler_is_idempotent() -> None:
    first = scheduler.start_scheduler()
    second = scheduler.start_scheduler()
    assert first is second
    assert scheduler.get_scheduler() is first


async def test_stop_scheduler_clears_it() -> None:
    scheduler.start_scheduler()
    scheduler.stop_scheduler()
    assert scheduler.get_scheduler() is None


def test_interval_hours_defaults_to_weekly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REFLECTION_JOB_INTERVAL_HOURS", raising=False)
    assert scheduler._interval_hours() == 24.0 * 7


def test_interval_hours_reads_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFLECTION_JOB_INTERVAL_HOURS", "12")
    assert scheduler._interval_hours() == 12.0


async def test_scheduler_registers_all_runtime_jobs() -> None:
    sched = scheduler.start_scheduler()
    assert {job.id for job in sched.get_jobs()} == {
        "reflection_job",
        "research_job",
        "engagement_job",
        "retention_job",
        "scheduled_posts_job",
    }


async def test_run_reflection_job_runs_against_a_real_db_session_and_handles_insufficient_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises _run_reflection_job() itself (not just run_reflection()

    directly) — the wiring of a fresh session factory + a real llm_client
    reference, end to end, for the insufficient-feedback (no-op) path so no
    live model call is needed.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    configure_engine(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    import app.database as database_module

    monkeypatch.setattr(
        database_module, "get_session_factory", lambda: async_sessionmaker(bind=engine, expire_on_commit=False)
    )

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as seed_db:
        await _add_active_user(seed_db)

    await scheduler._run_reflection_job()  # must not raise

    await engine.dispose()


async def test_run_reflection_job_logs_and_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scheduled background job must never crash the scheduler itself —

    confirmed by making run_reflection raise and checking _run_reflection_job
    still returns normally (and moves on to any other active user, though
    only one exists here).
    """

    async def failing_run_reflection(db, llm_client, days=7):
        raise RuntimeError("boom")

    import app.learning.reflection_job as reflection_job_module

    monkeypatch.setattr(reflection_job_module, "run_reflection", failing_run_reflection)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    configure_engine(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    import app.database as database_module

    monkeypatch.setattr(
        database_module, "get_session_factory", lambda: async_sessionmaker(bind=engine, expire_on_commit=False)
    )

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as seed_db:
        await _add_active_user(seed_db)

    await scheduler._run_reflection_job()  # must not raise despite the failure above

    await engine.dispose()


async def test_scheduled_posts_job_fans_out_per_user_and_isolates_failures(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the multi-tenant Stage 3 fan-out (see

    plans/peaceful-scribbling-tiger.md): every active user gets their own
    call, and one user's exception must never stop another user's from
    running.
    """
    import app.automation as automation_module

    failing_user = await _add_active_user(db_session, "scheduler-failing-user")
    ok_user = await _add_active_user(db_session, "scheduler-ok-user")
    calls: list[str] = []

    async def fake_process_due_posts(user_id):
        calls.append(user_id)
        if user_id == failing_user:
            raise RuntimeError("boom")
        return {"claimed": 1, "published": 1, "failed": 0}

    monkeypatch.setattr(automation_module, "process_due_posts", fake_process_due_posts)

    await scheduler._run_scheduled_posts_job()  # must not raise despite failing_user's exception

    assert set(calls) == {failing_user, ok_user}


async def test_retention_job_runs_policy_with_scoped_session(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.memory.policy as policy_module

    calls = 0

    async def fake_purge(db):
        nonlocal calls
        calls += 1
        assert db is not db_session
        return {"post_content_purged": 0, "thread_content_purged": 0}

    monkeypatch.setattr(policy_module, "run_retention_purge", fake_purge)

    await scheduler._run_retention_job()

    assert calls == 1


async def test_scheduled_posts_job_runs_due_queue(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.automation as automation_module

    user_id = await _add_active_user(db_session)
    calls: list[str] = []

    async def fake_process_due_posts(user_id):
        calls.append(user_id)
        return {"claimed": 1, "published": 1, "failed": 0}

    monkeypatch.setattr(automation_module, "process_due_posts", fake_process_due_posts)

    await scheduler._run_scheduled_posts_job()

    assert calls == [user_id]


async def test_research_job_honors_persisted_cadence(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agents.research_pipeline as pipeline_module
    from app.memory.settings import RESEARCH_AUTOMATION_QUERIES_KEY, set_setting
    from app.tenancy import context as tenancy_context

    user_id = await _add_active_user(db_session)
    token = tenancy_context.set_current_user_id(user_id)
    try:
        await set_setting(db_session, RESEARCH_AUTOMATION_QUERIES_KEY, "first topic,second topic", updated_by="test")
    finally:
        tenancy_context.reset_current_user_id(token)

    queries: list[str] = []

    async def fake_conduct_research(query, llm_client, persist):
        assert persist is True
        queries.append(query)

    monkeypatch.setattr(pipeline_module, "conduct_research", fake_conduct_research)

    await scheduler._run_research_job()
    await scheduler._run_research_job()

    assert queries == ["first topic", "second topic"]


async def test_research_job_skips_a_user_with_no_automation_queries_configured(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agents.research_pipeline as pipeline_module

    await _add_active_user(db_session)  # no research_agent.automation_queries set for them

    async def unexpected_conduct_research(query, llm_client, persist):
        raise AssertionError("must not run research for a user who hasn't configured any queries")

    monkeypatch.setattr(pipeline_module, "conduct_research", unexpected_conduct_research)

    await scheduler._run_research_job()  # must not raise


async def test_engagement_job_deduplicates_notifications(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agents.engagement as engagement_module
    import app.tools.registry as registry_module
    from app.models.automation import ProcessedNotificationRecord

    user_id = await _add_active_user(db_session)
    await _mark_connected(db_session, user_id, {"composio", "linkedin"})

    handled: list[str] = []

    async def fake_execute_tool(tool_name, arguments, approved):
        assert tool_name == "get_linkedin_notifications"
        assert approved is False
        return {
            "status": "success",
            "result": {
                "notifications": [
                    {"id": "notification-1", "type": "comment", "text": "Useful post"},
                ]
            },
        }

    async def fake_handle_notification(notification, llm_client, db):
        handled.append(notification["id"])
        return {"status": "submitted_for_approval"}

    monkeypatch.setattr(registry_module, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(engagement_module, "handle_notification", fake_handle_notification)

    await scheduler._run_engagement_job()
    await scheduler._run_engagement_job()

    record = await db_session.get(ProcessedNotificationRecord, "notification-1")
    assert handled == ["notification-1"]
    assert record is not None
    assert record.outcome == "submitted_for_approval"
    assert record.user_id == user_id


async def test_engagement_job_skips_a_user_without_a_linkedin_connection(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.tools.registry as registry_module

    await _add_active_user(db_session)  # composio/linkedin left unconfigured

    async def unexpected_execute_tool(tool_name, arguments, approved):
        raise AssertionError("must not poll LinkedIn for a user with no connection configured")

    monkeypatch.setattr(registry_module, "execute_tool", unexpected_execute_tool)

    await scheduler._run_engagement_job()  # must not raise


async def test_distributed_scheduler_skips_jobs_owned_by_another_worker(shared_redis) -> None:
    calls = 0

    async def job() -> None:
        nonlocal calls
        calls += 1

    shared_redis.set(scheduler._SCHEDULER_OWNER_KEY, "another-worker", ex=120)
    await scheduler._run_distributed_job("test_job", job, 60)
    assert calls == 0


async def test_distributed_scheduler_owner_runs_job_and_renews_lease(shared_redis) -> None:
    calls = 0

    async def job() -> None:
        nonlocal calls
        calls += 1

    await scheduler._run_distributed_job("test_job", job, 60)
    assert calls == 1
    assert shared_redis.get(scheduler._SCHEDULER_OWNER_KEY) == scheduler._scheduler_owner_id
    assert shared_redis.ttl(scheduler._SCHEDULER_OWNER_KEY) > 0
