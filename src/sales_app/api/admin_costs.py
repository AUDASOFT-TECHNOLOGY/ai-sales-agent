"""Admin API: technical costs by provider, the cost tariffs, and a client's usage against its quota.

- `GET  /admin/v1/costs?period=YYYY-MM[&client_id=]`: every provider's cost with its basis
  (`measured`, `estimated`, `unavailable`). Without `client_id` it spans all clients and is the
  owner's; with one, the usual client access rule applies (a client the admin cannot reach is a 404).
- `GET/PUT/DELETE /admin/v1/settings/costs/rates[/{provider}/{key}]`: the external prices the Admin
  provides (none by default: a missing price makes the cost `unavailable`, never zero). The owner
  changes them; any admin reads.
- `POST /admin/v1/costs/entries`, `DELETE /admin/v1/costs/entries/{id}`: a cost the Admin enters by
  hand (OVH, infra, Google, ...), with its basis. Owner only.
- `POST /admin/v1/costs/materialize`: keep the computed lines of a period in `provider_costs`.
- `GET  /admin/v1/clients/{client_id}/quota`: the client's consumption against its quota.

Every change is recorded in the central audit log (by the audit middleware). Amounts travel as
decimal strings, never floats; `null` is unknown, which is not 0.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import ensure_client_access, make_current_admin_dependency, require_role
from sales_app.api.admin_clients import client_wiring_and_document
from sales_app.api.client_usage import UsageResponse, usage_response
from sales_app.bootstrap import Application
from sales_app.provider_costs import (
    CostLine,
    CostRate,
    CostReport,
    CostService,
    InvalidCostError,
    parse_period,
    validate_entry,
    validate_rate,
)
from sales_app.sourcing.errors import StoreUnavailableError


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


class RateBody(BaseModel):
    provider: str = Field(max_length=32)
    key: str = Field(max_length=64)
    unit: str = Field(max_length=32)
    unit_price: Decimal
    currency: str = Field(max_length=3)


class RateResponse(BaseModel):
    provider: str
    key: str
    unit: str
    unit_price: str
    currency: str
    updated_at: datetime
    updated_by: str


class RatesResponse(BaseModel):
    rates: list[RateResponse]
    missing: list[str]  # tariffs the engine reads that the Admin has not provided yet


class EntryBody(BaseModel):
    client_id: str | None = Field(default=None, max_length=64)
    provider: str = Field(max_length=32)
    category: str = Field(max_length=64)
    period: str = Field(max_length=10, description="YYYY-MM or YYYY-MM-DD")
    quantity: Decimal | None = None
    unit: str = Field(max_length=32)
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    currency: str | None = Field(default=None, max_length=3)
    basis: Literal["measured", "estimated", "unavailable"]


class MaterializeBody(BaseModel):
    period: str = Field(max_length=10)
    client_id: str | None = Field(default=None, max_length=64)


class CostLineResponse(BaseModel):
    id: int | None
    client_id: str | None
    provider: str
    category: str
    detail: str
    period: str
    quantity: str | None
    unit: str
    unit_price: str | None
    amount: str | None
    currency: str | None
    basis: str
    source: str


class ProviderCostResponse(BaseModel):
    provider: str
    basis: str
    totals: dict[str, str]  # known amounts per currency (a lower bound unless `complete`)
    lines: int
    unavailable_lines: int
    complete: bool


class CostReportResponse(BaseModel):
    period: str
    client_id: str | None
    basis: str
    complete: bool
    totals: dict[str, str]
    providers: list[ProviderCostResponse]
    lines: list[CostLineResponse]


def _line(line: CostLine) -> CostLineResponse:
    return CostLineResponse(
        id=line.id,
        client_id=line.client_id,
        provider=line.provider,
        category=line.category,
        detail=line.detail,
        period=line.period.label,
        quantity=_money(line.quantity),
        unit=line.unit,
        unit_price=_money(line.unit_price),
        amount=_money(line.amount),
        currency=line.currency,
        basis=line.basis,
        source=line.source,
    )


def report_response(report: CostReport) -> CostReportResponse:
    return CostReportResponse(
        period=report.period.label,
        client_id=report.client_id,
        basis=report.basis,
        complete=report.complete,
        totals={c: format(v, "f") for c, v in report.totals.items()},
        providers=[
            ProviderCostResponse(
                provider=p.provider,
                basis=p.basis,
                totals={c: format(v, "f") for c, v in p.totals.items()},
                lines=p.lines,
                unavailable_lines=p.unavailable_lines,
                complete=p.complete,
            )
            for p in report.providers
        ],
        lines=[_line(line) for line in report.lines],
    )


def _rate(rate: CostRate) -> RateResponse:
    return RateResponse(
        provider=rate.provider,
        key=rate.key,
        unit=rate.unit,
        unit_price=format(rate.unit_price, "f"),
        currency=rate.currency,
        updated_at=rate.updated_at,
        updated_by=rate.updated_by,
    )


REQUIRED_RATES = ("meta_whatsapp/message_sent",)


def create_admin_costs_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    def service() -> CostService:
        if application.costs is None:
            raise HTTPException(503, "costs need the database (set SALES_AGENT_DATABASE_URL)")
        return application.costs

    def existing_client(admin: AdminUser, client_id: str) -> None:
        ensure_client_access(admin, client_id)
        if client_wiring_and_document(application, client_id) is None:
            raise HTTPException(404, "no such client")

    def unavailable() -> HTTPException:
        return HTTPException(503, "the cost register is unavailable")

    def rates_response(rates: list[CostRate]) -> RatesResponse:
        have = {f"{r.provider}/{r.key}" for r in rates}
        return RatesResponse(
            rates=[_rate(r) for r in rates], missing=[k for k in REQUIRED_RATES if k not in have]
        )

    @router.get("/costs", response_model=CostReportResponse)
    async def costs(
        period: str, client_id: str | None = None, admin: AdminUser = Depends(get_current_admin)
    ) -> CostReportResponse:
        if client_id is None:
            if admin.role is not AdminRole.OWNER:
                raise HTTPException(403, "costs across all clients are for the owner")
        else:
            existing_client(admin, client_id)
        try:
            when = parse_period(period)
            report = await run_in_threadpool(service().costs_by_provider, when, client_id)
        except InvalidCostError as error:
            raise HTTPException(422, str(error)) from None
        except StoreUnavailableError:
            raise unavailable() from None
        return report_response(report)

    @router.get("/settings/costs/rates", response_model=RatesResponse)
    async def list_rates(_admin: AdminUser = Depends(get_current_admin)) -> RatesResponse:
        try:
            return rates_response(await run_in_threadpool(service().list_rates))
        except StoreUnavailableError:
            raise unavailable() from None

    @router.put("/settings/costs/rates", response_model=RateResponse)
    async def set_rate(body: RateBody, admin: AdminUser = Depends(require_owner)) -> RateResponse:
        try:
            rate = validate_rate(
                body.provider,
                body.key,
                body.unit,
                body.unit_price,
                body.currency,
                updated_at=application.clock.now(),
                updated_by=admin.username,
            )
            await run_in_threadpool(service().set_rate, rate)
        except InvalidCostError as error:
            raise HTTPException(422, str(error)) from None
        except StoreUnavailableError:
            raise unavailable() from None
        return _rate(rate)

    @router.delete("/settings/costs/rates/{provider}/{key}", response_model=RatesResponse)
    async def delete_rate(
        provider: str, key: str, _admin: AdminUser = Depends(require_owner)
    ) -> RatesResponse:
        try:
            if not await run_in_threadpool(service().delete_rate, provider, key):
                raise HTTPException(404, "no such rate")
            return rates_response(await run_in_threadpool(service().list_rates))
        except StoreUnavailableError:
            raise unavailable() from None

    @router.post("/costs/entries", response_model=CostLineResponse, status_code=201)
    async def add_entry(
        body: EntryBody, admin: AdminUser = Depends(require_owner)
    ) -> CostLineResponse:
        if body.client_id is not None:
            existing_client(admin, body.client_id)
        try:
            line = validate_entry(
                client_id=body.client_id,
                provider=body.provider,
                category=body.category,
                period=body.period,
                quantity=body.quantity,
                unit=body.unit,
                unit_price=body.unit_price,
                amount=body.amount,
                currency=body.currency,
                basis=body.basis,
                by=admin.username,
            )
            saved = await run_in_threadpool(service().add_entry, line, by=admin.username)
        except InvalidCostError as error:
            raise HTTPException(422, str(error)) from None
        except StoreUnavailableError:
            raise unavailable() from None
        return _line(saved)

    @router.delete("/costs/entries/{entry_id}")
    async def delete_entry(
        entry_id: int, _admin: AdminUser = Depends(require_owner)
    ) -> dict[str, str]:
        try:
            if not await run_in_threadpool(service().delete_entry, entry_id):
                raise HTTPException(404, "no such cost entry")
        except StoreUnavailableError:
            raise unavailable() from None
        return {"status": "deleted"}

    @router.post("/costs/materialize")
    async def materialize(
        body: MaterializeBody, admin: AdminUser = Depends(require_owner)
    ) -> dict[str, int]:
        if body.client_id is not None:
            existing_client(admin, body.client_id)
        try:
            count = await run_in_threadpool(
                service().materialize,
                parse_period(body.period),
                body.client_id,
                by=admin.username,
            )
        except InvalidCostError as error:
            raise HTTPException(422, str(error)) from None
        except StoreUnavailableError:
            raise unavailable() from None
        return {"lines": count}

    @router.get("/clients/{client_id}/quota", response_model=UsageResponse)
    async def client_quota(
        client_id: str, admin: AdminUser = Depends(get_current_admin)
    ) -> UsageResponse:
        existing_client(admin, client_id)
        if application.quotas is None:
            raise HTTPException(503, "usage needs the database (set SALES_AGENT_DATABASE_URL)")
        status = await run_in_threadpool(application.quotas.status, client_id)
        return usage_response(status)

    return router
