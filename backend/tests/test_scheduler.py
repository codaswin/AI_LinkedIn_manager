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


async def test_scheduler_registers_the_reflection_job() -> None:
    sched = scheduler.start_scheduler()
    job = sched.get_job("reflection_job")
    assert job is not None


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
