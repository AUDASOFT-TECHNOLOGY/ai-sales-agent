"""Daily selection of commercial actions: due follow-ups and new prospects, capped per client."""

from sales_core.planner.daily_planner import (
    DEFAULT_DAILY_ACTION_LIMIT,
    DailyPlan,
    plan_daily_actions,
)

__all__ = ["DEFAULT_DAILY_ACTION_LIMIT", "DailyPlan", "plan_daily_actions"]
