"""Client API: the client's own dashboard (`GET /client/v1/dashboard?from&to`, spec 10.2).

Takes no client id: the session decides it, and every store is asked for that client only. Same
rules as the Admin dashboard: every indicator is `{value, availability}` (`measured`, `estimated`,
`not_tracked`), nothing is simulated, conversions are declared and confirmed. Nothing technical is shown (no
cost, token, provider or internal id): the integrations are the readiness sentences the client
already reads under `/client/v1/readiness`. See docs/dashboards.md.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool

from sales_app.api.client import make_current_client_dependency
from sales_app.api.client_agent import evaluate_readiness
from sales_app.api.client_billing import _invoice as invoice_json
from sales_app.api.client_usage import usage_response
from sales_app.api.dashboard_support import (
    NO_DATABASE,
    UNREADABLE,
    activity_of,
    client_metrics,
    incident_block,
    incident_series,
    read_notifications,
)
from sales_app.billing import InvoiceFilter, InvoiceStatus, next_cycle_date
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.dashboards import assemble
from sales_app.dashboards.figures import MEASURED, indicator
from sales_app.dashboards.ranges import DashboardRange, InvalidRangeError, days_of, resolve_range
from sales_app.metrics import Period
from sales_app.notifications import CLIENT, NotificationQuery
from sales_app.sourcing.errors import StoreUnavailableError

log = logging.getLogger(__name__)

PERFORMANCE_MONTHS = 12
RECENT_INVOICES = 6


class _ClientBuilder:
    def __init__(
        self,
        application: Application,
        client: ClientProfile,
        chosen: DashboardRange,
        now: datetime,
    ) -> None:
        self.app = application
        self.client = client
        self.client_id = client.client_id
        self.range = chosen
        self.now = now
        self.today = now.astimezone(UTC).date()

    def agent(self) -> dict[str, Any]:
        try:
            stored = self.app.agent_state.get(self.client_id)
            checks = evaluate_readiness(self.app, self.client)
        except Exception:
            log.warning("agent state of %s could not be read", self.client_id, exc_info=True)
            return {"availability": "not_tracked", "reason": UNREADABLE}
        ready = all(c.ok for c in checks)
        state = "not_started" if stored is None else "running" if stored.running else "paused"
        return {
            "availability": MEASURED,
            "state": state,
            "active": state == "running" and ready,
            "ready": ready,
            "changed_at": stored.changed_at.isoformat() if stored is not None else None,
            "integrations": [
                {"id": c.id, "ok": c.ok, "message": None if c.ok else c.message} for c in checks
            ],
        }

    def performance(self) -> dict[str, Any]:
        base = {
            "granularity": "month",
            "note": (
                "rate = prospects who replied / prospects contacted, month by month; conversions "
                "declared = prospects who said they paid, confirmed = payment confirmed by the client"
            ),
        }
        if self.app.metrics_readers is None:
            return {**base, "availability": "not_tracked", "reason": NO_DATABASE, "points": []}
        points = []
        for month in self.range.months()[-PERFORMANCE_MONTHS:]:
            year, mon = int(month[:4]), int(month[5:7])
            first = datetime(year, mon, 1, tzinfo=UTC)
            nxt = datetime(year + mon // 12, mon % 12 + 1, 1, tzinfo=UTC)
            one = client_metrics(
                self.app,
                self.client_id,
                Period(since=max(first, self.range.since), until=min(nxt, self.range.until)),
            )
            if one is None:
                return {**base, "availability": "not_tracked", "reason": UNREADABLE, "points": []}
            points.append(assemble.performance_point(month, [one]))
        return {**base, "availability": MEASURED, "reason": None, "points": points}

    def usage(self) -> dict[str, Any]:
        if self.app.quotas is None:
            return {"availability": "not_tracked", "reason": NO_DATABASE}
        status = usage_response(self.app.quotas.status(self.client_id), admin=False)
        return {"availability": MEASURED, **status.model_dump(mode="json", exclude={"days"})}

    def billing(self) -> dict[str, Any]:
        invoices, plans = self.app.invoices, self.app.plans
        if invoices is None or plans is None:
            return {"availability": "not_tracked", "reason": NO_DATABASE}
        try:
            mine = [
                i
                for i in invoices.list_invoices(InvoiceFilter(client_id=self.client_id))
                if i.client_id == self.client_id
            ]
            assignment = plans.current_assignment(self.client_id)
        except StoreUnavailableError:
            return {"availability": "not_tracked", "reason": UNREADABLE}
        unpaid = [i for i in mine if i.status in (InvoiceStatus.PENDING, InvoiceStatus.FAILED)]
        return {
            "availability": MEASURED,
            "subscription": {
                "status": "active" if assignment is not None else "no_plan",
                "plan_name": assignment.plan_name if assignment is not None else None,
                "monthly_price": format(assignment.monthly_price, "f") if assignment else None,
                "currency": assignment.currency if assignment is not None else None,
                "next_billing_at": next_cycle_date(assignment.started_at, self.now).isoformat()
                if assignment
                else None,
            },
            "invoices": {
                "issued": indicator(len(mine)),
                "paid": indicator(sum(1 for i in mine if i.is_collected)),
                "unpaid": indicator(len(unpaid)),
                "overdue": indicator(sum(1 for i in unpaid if i.is_overdue(self.now))),
                "recent": [
                    {
                        k: v
                        for k, v in invoice_json(i, self.now).items()
                        if k
                        in (
                            "number",
                            "period",
                            "issued_on",
                            "currency",
                            "amount",
                            "amount_after_discount",
                            "status",
                            "collected",
                            "overdue",
                            "due_at",
                        )
                    }
                    for i in mine[:RECENT_INVOICES]
                ],
            },
        }

    def build(self) -> dict[str, Any]:
        period = Period(since=self.range.since, until=self.range.until)
        activity, metrics = activity_of(self.app, [self.client_id], period)
        days = days_of(self.range, self.today)
        if not metrics:
            reason = NO_DATABASE if self.app.metrics_readers is None else UNREADABLE
            activity_series: dict[str, Any] = {
                "availability": "not_tracked",
                "reason": reason,
                "points": [],
            }
        else:
            activity_series = {
                "availability": MEASURED,
                "reason": None,
                "granularity": self.range.granularity,
                "points": assemble.activity_series(metrics, days, self.range.granularity),
            }

        def mine(**more: Any) -> NotificationQuery:
            return NotificationQuery(audience=CLIENT, client_id=self.client_id, **more)

        open_read = read_notifications(self.app, mine, include_resolved=False)
        all_read = read_notifications(self.app, mine, include_resolved=True)
        incidents = incident_block(open_read, all_read)
        for item in incidents["items"]:
            item.pop("client_id", None)
        return {
            "generated_at": self.now.isoformat(),
            "period": {
                **self.range.label(),
                "months": self.range.months(),
                "granularity": self.range.granularity,
            },
            "agent": self.agent(),
            "activity": activity,
            "human_requests": activity["needs_human"],
            "incidents": incidents,
            "usage": self.usage(),
            "billing": self.billing(),
            "series": {
                "activity": activity_series,
                "performance": self.performance(),
                "incidents": incident_series(all_read, self.range, days),
            },
        }


def create_client_dashboard_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])

    @router.get("/dashboard")
    async def dashboard(
        response: Response,
        from_: str | None = Query(default=None, alias="from", description="YYYY-MM or YYYY-MM-DD"),
        to: str | None = Query(default=None, description="YYYY-MM or YYYY-MM-DD"),
        client: ClientProfile = Depends(get_current_client),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        now = application.clock.now()
        try:
            chosen = resolve_range(from_, to, now)
        except InvalidRangeError as error:
            raise HTTPException(422, str(error)) from None
        return await run_in_threadpool(_ClientBuilder(application, client, chosen, now).build)

    return router
