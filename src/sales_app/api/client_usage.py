"""Client API: the client's own usage against its plan's quota.

`GET /client/v1/usage`. It takes no client id: the session decides it. Every figure is read from the
real send counters (`usage_basis: measured`); the credits consumed this month come from the LLM
usage register (`credits_basis: measured`, `unavailable` when it cannot be read). `state` is `ok`, `warning` (from 80%), `reached`, `not_configured` or `unavailable`, with
a plain `message` and the `action` to take. Nothing technical (no store, token or cost) is shown.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sales_app.api.client import make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.quotas import QuotaStatus


class UsageResponse(BaseModel):
    client_id: str
    plan_name: str | None
    day_utc: str
    month: str
    daily_limit: int | None
    daily_used: int | None
    daily_remaining: int | None
    daily_percent: int | None
    month_used: int | None
    included_credits: int | None
    credits_used: int | None  # this month; null: not available
    credits_basis: str  # measured | unavailable
    usage_basis: str  # measured
    state: str
    message: str
    action: str
    plan_daily_quota: int | None = None
    admin_cap: int | None = None
    month_limit: int | None = None
    month_actions_used: int | None = None
    month_percent: int | None = None
    credits_today: int | None = None
    credits_remaining: int | None = None
    credits_percent: int | None = None
    calls: int | None = None
    calls_without_figures: int | None = None
    days: list[dict[str, object]] = []


def usage_response(status: QuotaStatus, *, admin: bool = True) -> UsageResponse:
    """`admin=False` leaves out the Admin's own cap and the plan's raw quota."""
    return UsageResponse(
        client_id=status.client_id,
        plan_name=status.plan_name,
        day_utc=status.day_utc,
        month=status.month,
        daily_limit=status.daily_limit,
        daily_used=status.daily_used,
        daily_remaining=status.daily_remaining,
        daily_percent=status.daily_percent,
        month_used=status.month_used,
        included_credits=status.included_credits,
        credits_used=status.credits_used,
        credits_basis=status.credits_basis,
        usage_basis=status.usage_basis,
        state=status.state,
        message=status.message,
        action=status.action,
        plan_daily_quota=status.plan_daily_quota if admin else None,
        admin_cap=status.admin_cap if admin else None,
        month_limit=status.month_limit,
        month_actions_used=status.month_actions_used,
        month_percent=status.month_percent,
        credits_today=status.credits_today,
        credits_remaining=status.credits_remaining,
        credits_percent=status.credits_percent,
        calls=status.calls,
        calls_without_figures=status.calls_without_figures,
        days=list(status.days),
    )


def create_client_usage_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])

    @router.get("/usage", response_model=UsageResponse)
    async def usage(client: ClientProfile = Depends(get_current_client)) -> UsageResponse:
        if application.quotas is None:
            raise HTTPException(503, "usage is not available right now")
        status = await run_in_threadpool(application.quotas.status, client.client_id)
        return usage_response(status, admin=False)

    return router
