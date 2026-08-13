from __future__ import annotations

import asyncio

import pytest
from app import activity as activity_module
from app.activity import activity, clear_activity, get_activity, set_activity


@pytest.fixture(autouse=True)
def _reset() -> None:
    activity_module.reset_for_testing()
    yield
    activity_module.reset_for_testing()


def test_idle_by_default() -> None:
    assert get_activity() is None


def test_set_then_get() -> None:
    set_activity("research", "researching", detail="Searching Reddit", source="reddit")
    result = get_activity()
    assert result is not None
    assert result["agent"] == "research"
    assert result["action"] == "researching"
    assert result["detail"] == "Searching Reddit"
    assert result["source"] == "reddit"
    assert result["elapsed_seconds"] >= 0.0


def test_clear_with_no_token_wipes_everything() -> None:
    set_activity("research", "researching")
    set_activity("analytics", "analyzing")
    clear_activity()
    assert get_activity() is None


def test_clear_with_token_removes_only_that_entry() -> None:
    token_a = set_activity("research", "researching", source="reddit")
    token_b = set_activity("analytics", "analyzing")
    assert token_a != token_b

    clear_activity(token_a)
    remaining = get_activity()
    assert remaining is not None
    assert remaining["agent"] == "analytics"


def test_context_manager_clears_on_normal_exit() -> None:
    with activity("research", "researching"):
        assert get_activity() is not None
    assert get_activity() is None


def test_context_manager_clears_on_exception() -> None:
    with pytest.raises(RuntimeError):
        with activity("research", "researching"):
            assert get_activity() is not None
            raise RuntimeError("boom")
    assert get_activity() is None


async def test_concurrent_activities_dont_stomp_each_other() -> None:
    """Regression test for the exact bug found during manual verification:

    a single global slot let whichever concurrent task finished FIRST clear
    the board, hiding every other still-running task — sometimes for the
    entire remaining duration of a request. This reproduces that shape
    directly: a short task and a long task running concurrently, and
    asserts the board is still non-idle for the ENTIRE duration of the
    longer task, not just until the shorter one exits.
    """

    async def short_task() -> None:
        with activity("research", "researching", source="short"):
            await asyncio.sleep(0.05)

    async def long_task() -> None:
        with activity("research", "researching", source="long"):
            await asyncio.sleep(0.3)

    async def assert_never_idle_during(seconds: float) -> None:
        elapsed = 0.0
        step = 0.02
        while elapsed < seconds:
            assert get_activity() is not None, f"board went idle at t={elapsed:.2f}s while long_task should still be running"
            await asyncio.sleep(step)
            elapsed += step

    await asyncio.gather(short_task(), long_task(), assert_never_idle_during(0.25))
    assert get_activity() is None


async def test_get_activity_reports_most_recently_started_when_concurrent() -> None:
    async def first() -> None:
        with activity("research", "researching", source="first"):
            await asyncio.sleep(0.2)

    async def second_starts_later() -> None:
        await asyncio.sleep(0.05)
        with activity("research", "researching", source="second"):
            await asyncio.sleep(0.05)
            result = get_activity()
            assert result is not None
            assert result["source"] == "second"

    await asyncio.gather(first(), second_starts_later())
