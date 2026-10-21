"""Admin API: financial aggregations (`GET /admin/v1/finance/summary`, spec 13.5).

Query: `from` and `to` (months `YYYY-MM`, inclusive; default: the 12 months ending with the last
month that has ended) and an optional `client_id`. An admin sees only the clients they can reach: a
`client_id` outside their scope is a 404, and without `client_id` the figures cover their clients
only (an owner: all). The contract is documented in docs/finance-contracts.md. Amounts are decimal
strings, never added across currencies. Read only.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import make_current_admin_dependency
from sales_app.billing import InvalidPeriodError, parse_period
from sales_app.billing_service import recent_months
from sales_app.bootstrap import Application
from sales_app.finance.contracts import FinanceSummary
from sales_app.finance.service import FinanceService, StoreFinanceReader
from sales_app.sourcing.errors import StoreUnavailableError


class TotalsResponse(BaseModel):
    currency: str
    billed: str
    after_discount: str
    collected: str
    free: str
    discounted: str
    pending: str
    overdue: str
    invoices_issued: int
    invoices_paid: int
    invoices_free: int
    invoices_discounted: int
    invoices_pending: int
    invoices_overdue: int


class TimelineResponse(BaseModel):
    period: str
    currency: str
    billed: str
    collected: str


class SubscriptionPointResponse(BaseModel):
    period: str
    active: int
    inactive: int


class ProfitabilityResponse(BaseModel):
    status: str  # "not_connected" until the provider costs are wired
    costs: dict[str, str]
    margin: dict[str, str]


class FinanceSummaryResponse(BaseModel):
    period_from: str
    period_to: str
    client_id: str | None
    generated_at: datetime
    totals: list[TotalsResponse]
    timeline: list[TimelineResponse]
    subscriptions: list[SubscriptionPointResponse]
    profitability: ProfitabilityResponse


def _money(value) -> str:
    return format(value, "f")


def _response(summary: FinanceSummary) -> FinanceSummaryResponse:
    return FinanceSummaryResponse(
        period_from=summary.period_from,
        period_to=summary.period_to,
        client_id=summary.client_id,
        generated_at=summary.generated_at,
        totals=[
            TotalsResponse(
                currency=t.currency,
                billed=_money(t.billed),
                after_discount=_money(t.after_discount),
                collected=_money(t.collected),
                free=_money(t.free),
                discounted=_money(t.discounted),
                pending=_money(t.pending),
                overdue=_money(t.overdue),
                invoices_issued=t.issued,
                invoices_paid=t.paid,
                invoices_free=t.free_count,
                invoices_discounted=t.discounted_count,
                invoices_pending=t.pending_count,
                invoices_overdue=t.overdue_count,
            )
            for t in summary.totals
        ],
        timeline=[
            TimelineResponse(
                period=p.period,
                currency=p.currency,
                billed=_money(p.billed),
                collected=_money(p.collected),
            )
            for p in summary.timeline
        ],
        subscriptions=[
            SubscriptionPointResponse(period=s.period, active=s.active, inactive=s.inactive)
            for s in summary.subscriptions
        ],
        profitability=ProfitabilityResponse(
            status=summary.profitability.status,
            costs={c: _money(v) for c, v in summary.profitability.costs.items()},
            margin={c: _money(v) for c, v in summary.profitability.margin.items()},
        ),
    )


def create_admin_finance_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    router = APIRouter(prefix="/admin/v1/finance", tags=["admin"])

    def service() -> FinanceService:
        if application.finance is not None:
            return application.finance
        if application.invoices is not None and application.plans is not None:
            return FinanceService(StoreFinanceReader(application.invoices, application.plans))
        raise HTTPException(503, "finance needs the database (set SALES_AGENT_DATABASE_URL)")

    @router.get("/summary", response_model=FinanceSummaryResponse)
    async def summary(
        from_: str | None = Query(default=None, alias="from", description="First month, YYYY-MM"),
        to: str | None = Query(default=None, description="Last month, YYYY-MM"),
        client_id: str | None = None,
        admin: AdminUser = Depends(get_current_admin),
    ) -> FinanceSummaryResponse:
        finance = service()
        now = application.clock.now()
        last = recent_months(now, 1)[0]
        first = recent_months(now, 12)[0]
        period_to, period_from = to or last, from_ or (first if to is None else None)
        try:
            for value in (period_from, period_to):
                if value is not None:
                    parse_period(value)
            if period_from is None:  # `to` given alone: the 12 months ending with it
                year, month = parse_period(period_to)[0].year, parse_period(period_to)[0].month
                index = year * 12 + month - 1 - 11
                period_from = f"{index // 12:04d}-{index % 12 + 1:02d}"
        except InvalidPeriodError as error:
            raise HTTPException(422, str(error)) from None
        if client_id is not None and not admin.can_reach(client_id):
            raise HTTPException(404, "no such client")
        scope = None if admin.role is AdminRole.OWNER else frozenset(admin.client_ids)
        try:
            result = await run_in_threadpool(
                lambda: finance.summary(
                    period_from=period_from,
                    period_to=period_to,
                    now=now,
                    client_id=client_id,
                    client_ids=scope,
                )
            )
        except InvalidPeriodError as error:
            raise HTTPException(422, str(error)) from None
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        return _response(result)

    return router
