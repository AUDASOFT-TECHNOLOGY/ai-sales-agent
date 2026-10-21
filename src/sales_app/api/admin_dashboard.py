"""Admin API: the aggregated dashboard (`GET /admin/v1/dashboard?from&to&client_id`, spec 10.1).

One read-only payload that assembles ONLY what the other lots already compute: the clients and
agents, the commercial metrics (lot 2B), the technical costs (lot 2C), the invoices and the
subscriptions (lot 3), the plan quotas, the notifications and the runtime health. Nothing is
simulated: every indicator is `{value, availability}` with `availability` one of `measured`,
`estimated`, `not_tracked` (value null + a reason). Conversions are declared (prospect said they paid) and confirmed (client confirmed).

Scope: an owner sees every client; another role only the clients it can reach, and a `client_id`
outside them is a 404. See docs/dashboards.md.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency
from sales_app.api.admin_agents import _all_summaries as all_agent_summaries
from sales_app.api.admin_clients import _all_summaries as all_client_summaries
from sales_app.api.admin_finance import _response as finance_json
from sales_app.api.dashboard_support import (
    NO_DATABASE,
    UNREADABLE,
    activity_of,
    client_metrics,
    incident_block,
    incident_series,
    read_notifications,
)
from sales_app.billing import InvalidPeriodError
from sales_app.bootstrap import Application
from sales_app.dashboards import assemble
from sales_app.dashboards.figures import MEASURED, indicator, not_tracked
from sales_app.dashboards.ranges import DashboardRange, InvalidRangeError, days_of, resolve_range
from sales_app.finance.service import FinanceService, StoreFinanceReader
from sales_app.metrics import Period
from sales_app.notifications import ADMIN, NotificationQuery
from sales_app.provider_costs import CostReport, parse_period
from sales_app.sourcing.errors import StoreUnavailableError

log = logging.getLogger(__name__)

PERFORMANCE_MONTHS = 12  # distinct-prospect rates are computed month by month, at most this many


def _finance_service(application: Application) -> FinanceService | None:
    if application.finance is not None:
        return application.finance
    if application.invoices is not None and application.plans is not None:
        return FinanceService(StoreFinanceReader(application.invoices, application.plans))
    return None


def _month_period(month: str, chosen: DashboardRange) -> Period:
    year, mon = int(month[:4]), int(month[5:7])
    first = datetime(year, mon, 1, tzinfo=UTC)
    nxt = datetime(year + mon // 12, mon % 12 + 1, 1, tzinfo=UTC)
    return Period(since=max(first, chosen.since), until=min(nxt, chosen.until))


class _Builder:
    def __init__(
        self,
        application: Application,
        admin: AdminUser,
        client_id: str | None,
        chosen: DashboardRange,
        now: datetime,
    ) -> None:
        self.app = application
        self.admin = admin
        self.client_id = client_id
        self.range = chosen
        self.now = now
        self.today = now.astimezone(UTC).date()
        clients = [s for s in all_client_summaries(application) if admin.can_reach(s.client_id)]
        if client_id is not None:
            clients = [s for s in clients if s.client_id == client_id]
            if not clients:
                raise HTTPException(404, "no such client")
        self.clients = clients
        self.client_ids = [s.client_id for s in clients]
        self.whole_platform = client_id is None and admin.role is AdminRole.OWNER

    # ---- identity of the platform --------------------------------------------------------

    def platform(self) -> dict[str, Any]:
        active = sum(1 for c in self.clients if c.status == "active")
        models = sorted({c.llm_model for c in self.clients if c.llm_model})
        agents = [a for a in all_agent_summaries(self.app) if a.client_id in self.client_ids]
        return {
            "clients": {
                "total": indicator(len(self.clients)),
                "active": indicator(active),
                "inactive": indicator(len(self.clients) - active),
            },
            "llm_models": {"configured": indicator(len(models)), "models": models},
            "agents": {
                "total": indicator(len(agents)),
                "active": indicator(sum(1 for a in agents if a.status == "active")),
            },
            "clients_list": [
                {"client_id": c.client_id, "display_name": c.display_name, "status": c.status}
                for c in self.clients
            ],
        }

    # ---- commercial activity and performance ---------------------------------------------

    def activity(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        period = Period(since=self.range.since, until=self.range.until)
        block, metrics = activity_of(self.app, self.client_ids, period)
        days = days_of(self.range, self.today)
        if self.app.metrics_readers is None or not metrics and self.client_ids:
            reason = NO_DATABASE if self.app.metrics_readers is None else UNREADABLE
            series = {"availability": "not_tracked", "reason": reason, "points": []}
        else:
            series = {
                "availability": MEASURED,
                "reason": None,
                "granularity": self.range.granularity,
                "points": assemble.activity_series(metrics, days, self.range.granularity),
            }
        return block, series, self.performance()

    def performance(self) -> dict[str, Any]:
        months = self.range.months()[-PERFORMANCE_MONTHS:]
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
        for month in months:
            found = []
            for client_id in self.client_ids:
                one = client_metrics(self.app, client_id, _month_period(month, self.range))
                if one is None:
                    return {
                        **base,
                        "availability": "not_tracked",
                        "reason": UNREADABLE,
                        "points": [],
                    }
                found.append(one)
            points.append(assemble.performance_point(month, found))
        return {**base, "availability": MEASURED, "reason": None, "points": points}

    # ---- finance ---------------------------------------------------------------------------

    def finance(self) -> tuple[dict[str, Any], dict[Any, Any]]:
        service = _finance_service(self.app)
        if service is None:
            return {"availability": "not_tracked", "reason": NO_DATABASE}, {}
        months = self.range.months()
        scope = None if self.whole_platform else frozenset(self.client_ids)
        try:
            summary = service.summary(
                period_from=months[0],
                period_to=months[-1],
                now=self.now,
                client_id=self.client_id,
                client_ids=scope,
            )
        except (StoreUnavailableError, InvalidPeriodError):
            return {"availability": "not_tracked", "reason": UNREADABLE}, {}
        body = finance_json(summary).model_dump(mode="json")
        body.pop("profitability", None)  # the dashboard computes its own, with the costs
        body["availability"] = MEASURED
        collected = {t.currency: t.collected for t in summary.totals}
        return body, collected

    # ---- costs -----------------------------------------------------------------------------

    def _reports(self, month: str) -> list[CostReport]:
        costs = self.app.costs
        assert costs is not None
        period = parse_period(month)
        if self.whole_platform:
            return [costs.costs_by_provider(period, None)]
        return [costs.costs_by_provider(period, cid) for cid in self.client_ids]

    def costs(self, collected: dict[Any, Decimal]) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.app.costs is None:
            gone = {"availability": "not_tracked", "reason": NO_DATABASE}
            return gone, {"status": "unavailable", "reason": NO_DATABASE, "margin": {}}
        current = f"{self.today.year:04d}-{self.today.month:02d}"
        months = [m for m in self.range.months() if m <= current]
        try:
            per_month = {m: self._reports(m) for m in months}
        except StoreUnavailableError:
            gone = {"availability": "not_tracked", "reason": UNREADABLE}
            return gone, {"status": "unavailable", "reason": UNREADABLE, "margin": {}}
        merged = assemble.merge_costs([r for reports in per_month.values() for r in reports])
        series = [
            assemble.cost_series_point(m, assemble.merge_costs(reports))
            for m, reports in per_month.items()
        ]
        closed = [m for m in months if m < current]
        closed_costs = assemble.merge_costs([r for m in closed for r in per_month[m]])
        profit = (
            assemble.profitability(collected, closed_costs)
            if closed
            else {
                "status": "unavailable",
                "reason": "no closed month in the range: revenue and costs are compared month by month",
                "margin": {},
            }
        )
        profit["months"] = closed
        block = {
            **assemble.public_costs(merged),
            "months": months,
            "series": series,
            "note": "costs are counted by whole calendar month (UTC)",
        }
        return block, profit

    # ---- usage, reliability, integrations ----------------------------------------------------

    def usage(self) -> dict[str, Any]:
        quotas = self.app.quotas
        if quotas is None:
            return {"availability": "not_tracked", "reason": NO_DATABASE, "clients": []}
        rows = []
        for c in self.clients:
            s = quotas.status(c.client_id)
            rows.append(
                {
                    "client_id": c.client_id,
                    "display_name": c.display_name,
                    "plan_name": s.plan_name,
                    "state": s.state,
                    "daily_limit": s.daily_limit,
                    "daily_used": s.daily_used,
                    "daily_percent": s.daily_percent,
                    "month_used": s.month_used,
                    "credits": not_tracked("no credit-per-message rule is defined"),
                    "availability": MEASURED,
                }
            )
        return {"availability": MEASURED, "reason": None, "clients": rows}

    def _query(self, **more: Any) -> NotificationQuery:
        if self.whole_platform:
            scope = None
        else:
            scope = frozenset(self.client_ids)
        return NotificationQuery(audience=ADMIN, client_ids=scope, client_id=self.client_id, **more)

    def reliability(self, metrics_errors: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        open_read = read_notifications(self.app, self._query, include_resolved=False)
        all_read = read_notifications(self.app, self._query, include_resolved=True)
        days = days_of(self.range, self.today)
        health = [
            h for h in self.app.registry.health() if str(h.get("client_id")) in self.client_ids
        ]
        degraded = [h for h in health if h.get("status") == "degraded"]
        block = {
            **incident_block(open_read, all_read),
            "failed_actions": metrics_errors,
            "health": {
                "degraded_clients": indicator(len(degraded)),
                "clients": [
                    {
                        "client_id": str(h.get("client_id")),
                        "status": str(h.get("status")),
                        "consecutive_failures": h.get("consecutive_failures", 0),
                        "last_error_at": h.get("last_error_at"),
                    }
                    for h in health
                ],
            },
        }
        return block, incident_series(all_read, self.range, days)

    def integrations(self) -> dict[str, Any]:
        rows = []
        for cid in self.client_ids:
            if self.app.registry.get_config(cid) is None:
                rows.append({"client_id": cid, "readiness": "not_loaded", "missing": 0})
                continue
            missing = self.app.registry.missing(cid)
            applied = self.app.runtime_manager.status(cid)
            rows.append(
                {
                    "client_id": cid,
                    "readiness": "not_ready" if missing else "ready",
                    "missing": len(missing),
                    "apply_state": applied.state,
                    "apply_errors": len(applied.errors),
                }
            )
        ready = sum(1 for r in rows if r["readiness"] == "ready")
        return {
            "availability": MEASURED,
            "ready": indicator(ready),
            "not_ready": indicator(sum(1 for r in rows if r["readiness"] == "not_ready")),
            "clients": rows,
        }

    # ---- the whole payload -----------------------------------------------------------------

    def build(self) -> dict[str, Any]:
        platform = self.platform()
        activity, activity_series, performance = self.activity()
        finance, collected = self.finance()
        costs, profit = self.costs(collected)
        reliability, reliability_series = self.reliability(activity["with_errors"])
        return {
            "generated_at": self.now.isoformat(),
            "period": {
                **self.range.label(),
                "months": self.range.months(),
                "granularity": self.range.granularity,
            },
            "client_id": self.client_id,
            "scope": "all" if self.whole_platform else "scoped",
            **platform,
            "activity": activity,
            "finance": finance,
            "costs": costs,
            "profitability": profit,
            "usage": self.usage(),
            "reliability": reliability,
            "integrations": self.integrations(),
            "series": {
                "activity": activity_series,
                "performance": performance,
                "finance": {
                    "availability": finance.get("availability", "not_tracked"),
                    "timeline": finance.get("timeline", []),
                },
                "subscriptions": {
                    "availability": finance.get("availability", "not_tracked"),
                    "points": finance.get("subscriptions", []),
                },
                "costs": {
                    "availability": costs.get("availability", "not_tracked"),
                    "points": costs.get("series", []),
                },
                "reliability": reliability_series,
            },
        }


def create_admin_dashboard_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    @router.get("/dashboard")
    async def dashboard(
        response: Response,
        from_: str | None = Query(default=None, alias="from", description="YYYY-MM or YYYY-MM-DD"),
        to: str | None = Query(default=None, description="YYYY-MM or YYYY-MM-DD"),
        client_id: str | None = Query(default=None, max_length=64),
        admin: AdminUser = Depends(get_current_admin),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        if client_id is not None:
            ensure_client_access(admin, client_id)  # 404 before anything else
        now = application.clock.now()
        try:
            chosen = resolve_range(from_, to, now)
        except InvalidRangeError as error:
            raise HTTPException(422, str(error)) from None
        builder = _Builder(application, admin, client_id, chosen, now)
        return await run_in_threadpool(builder.build)

    return router
