"""Daily LLM spend tracking shared across workers through Redis."""

from __future__ import annotations

import os
from datetime import datetime, time, timedelta, timezone

from app.shared_state import get_client
from app.tenancy.context import get_current_user_id

_DEFAULT_DAILY_BUDGET_USD = 10.0


class CostBudgetExceededError(RuntimeError):
    def __init__(self, spent_usd: float, budget_usd: float) -> None:
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        super().__init__(
            f"Daily LLM cost budget exceeded: spent ${spent_usd:.4f} of ${budget_usd:.2f} cap. "
            "Refusing further LLM calls until the daily window resets."
        )


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _redis_key() -> str:
    return f"safety:cost:{get_current_user_id()}:{_today_key()}"


def _ttl() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return max(60, int((tomorrow - now).total_seconds()) + 60)


def _budget_usd() -> float:
    raw = os.environ.get("LLM_COST_BUDGET_DAILY_USD")
    return _DEFAULT_DAILY_BUDGET_USD if raw is None or raw == "" else float(raw)


def get_today_spend() -> float:
    return float(get_client().get(_redis_key()) or 0.0)


def get_cost_summary() -> dict[str, float]:
    return {"today_usd": get_today_spend(), "budget_usd": _budget_usd()}


def check_budget() -> None:
    budget = _budget_usd()
    spent = get_today_spend()
    if spent >= budget:
        raise CostBudgetExceededError(spent_usd=spent, budget_usd=budget)


def record_cost(amount_usd: float) -> None:
    if amount_usd < 0:
        raise ValueError(f"amount_usd must be >= 0, got {amount_usd}")
    client = get_client()
    key = _redis_key()
    with client.pipeline() as pipe:
        pipe.incrbyfloat(key, amount_usd)
        pipe.expire(key, _ttl())
        pipe.execute()


def reset_for_testing() -> None:
    client = get_client()
    keys = list(client.scan_iter(match="safety:cost:*"))
    if keys:
        client.delete(*keys)
