from __future__ import annotations

import pytest
from app.database import Base, configure_engine
from app.learning import scheduler
from app.models.approval_request import ApprovalRequestRecord  # noqa: F401
from app.models.feedback import FeedbackRecord  # noqa: F401
from app.models.learning_proposal import LearningProposalRecord  # noqa: F401
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


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

    await scheduler._run_reflection_job()  # must not raise

    await engine.dispose()


async def test_run_reflection_job_logs_and_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scheduled background job must never crash the scheduler itself —

    confirmed by making run_reflection raise and checking _run_reflection_job
    still returns normally.
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

    await scheduler._run_reflection_job()  # must not raise despite the failure above

    await engine.dispose()


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


async def test_scheduled_posts_job_runs_due_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.automation as automation_module

    calls = 0

    async def fake_process_due_posts():
        nonlocal calls
        calls += 1
        return {"claimed": 1, "published": 1, "failed": 0}

    monkeypatch.setattr(automation_module, "process_due_posts", fake_process_due_posts)

    await scheduler._run_scheduled_posts_job()

    assert calls == 1


async def test_research_job_honors_persisted_cadence(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agents.research_pipeline as pipeline_module

    queries: list[str] = []

    async def fake_conduct_research(query, llm_client, persist):
        assert persist is True
        queries.append(query)

    monkeypatch.setattr(pipeline_module, "conduct_research", fake_conduct_research)
    monkeypatch.setenv("RESEARCH_AUTOMATION_QUERIES", "first topic,second topic")

    await scheduler._run_research_job()
    await scheduler._run_research_job()

    assert queries == ["first topic", "second topic"]


async def test_engagement_job_deduplicates_notifications(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agents.engagement as engagement_module
    import app.tools.registry as registry_module
    from app.models.automation import ProcessedNotificationRecord

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
