from __future__ import annotations

import pytest
from app.tenancy import context as tenancy_context
from app.tools.rate_limit import RateLimitExceededError, daily_rate_limiter


@pytest.fixture(autouse=True)
def _tenancy_context():
    token = tenancy_context.set_current_user_id("rate-limit-test-user")
    yield
    tenancy_context.reset_current_user_id(token)


def test_check_and_increment_counts_up_to_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_RATE_LIMIT_CAP", "2")
    assert daily_rate_limiter.check_and_increment("do_thing", "TEST_RATE_LIMIT_CAP") == 1
    assert daily_rate_limiter.check_and_increment("do_thing", "TEST_RATE_LIMIT_CAP") == 2
    with pytest.raises(RateLimitExceededError):
        daily_rate_limiter.check_and_increment("do_thing", "TEST_RATE_LIMIT_CAP")


def test_each_user_gets_their_own_independent_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: the Redis counter key used to be shared across every

    user (one global daily cap for the whole deployment) — see
    plans/peaceful-scribbling-tiger.md Stage 3. User A maxing out their cap
    must never block user B at the same configured limit.
    """
    monkeypatch.setenv("TEST_RATE_LIMIT_CAP", "1")

    token_a = tenancy_context.set_current_user_id("rate-limit-user-a")
    try:
        assert daily_rate_limiter.check_and_increment("do_thing", "TEST_RATE_LIMIT_CAP") == 1
        with pytest.raises(RateLimitExceededError):
            daily_rate_limiter.check_and_increment("do_thing", "TEST_RATE_LIMIT_CAP")
    finally:
        tenancy_context.reset_current_user_id(token_a)

    token_b = tenancy_context.set_current_user_id("rate-limit-user-b")
    try:
        # User B is unaffected by user A having already exhausted their cap.
        assert daily_rate_limiter.check_and_increment("do_thing", "TEST_RATE_LIMIT_CAP") == 1
    finally:
        tenancy_context.reset_current_user_id(token_b)
