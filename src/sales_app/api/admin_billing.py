"""Admin API for monthly invoices (see `sales_app.billing`).

Any admin reads the invoices of the clients they can reach (a client outside their scope does not
appear, and its invoice is a 404). Only an owner generates invoices or settles them. Generating a
month builds, for every client that was on a plan that month, one invoice from the terms recorded in
its plan assignments; an invoice that already exists is never rebuilt. Marking an invoice PAID or
FOR_FREE is the Admin's declaration, once (origin `manual`, traced); an invoice already paid by a
verified electronic confirmation (`POST /api/payments/webhook`, see `sales_app.payment_confirmation`)
refuses it with a 409. `overdue` is computed: unpaid (PENDING/FAILED) `DUE_DAYS` after its month.

Amounts travel as decimal strings, never floats.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import make_current_admin_dependency, require_role
from sales_app.billing import (
    InvalidPeriodError,
    Invoice,
    InvoiceFilter,
    InvoiceNotFoundError,
    InvoiceStatus,
    InvoiceStatusError,
    MixedCurrenciesError,
    PaymentRecord,
    build_invoice,
    ensure_closed,
    parse_period,
)
from sales_app.bootstrap import Application
from sales_app.sourcing.errors import StoreUnavailableError


class InvoiceLineResponse(BaseModel):
    plan_id: str
    plan_name: str
    monthly_price: str
    currency: str
    included_credits: int
    daily_quota: int
    days: int
    days_in_period: int
    amount: str


class InvoiceResponse(BaseModel):
    invoice_id: int
    number: str
    client_id: str
    period: str
    currency: str
    amount: str
    status: InvoiceStatus
    lines: list[InvoiceLineResponse]
    created_at: datetime
    created_by: str
    status_changed_at: datetime | None
    status_changed_by: str | None
    payment_origin: str | None  # "manual" | "electronic" | None while unpaid / free
    payment_transaction_id: str | None
    payment_event_id: str | None
    due_at: datetime
    overdue: bool


class PaymentEventResponse(BaseModel):
    event_id: str
    source: str
    invoice_id: int | None
    client_id: str | None
    invoice_reference: str | None
    amount: str | None
    currency: str | None
    transaction_id: str | None
    event_status: str
    outcome: str
    actor: str
    received_at: datetime


class PaymentEventsResponse(BaseModel):
    events: list[PaymentEventResponse]


class InvoicesResponse(BaseModel):
    invoices: list[InvoiceResponse]


class GenerateBody(BaseModel):
    period: str  # YYYY-MM, a month that has ended


class SkippedClient(BaseModel):
    client_id: str
    reason: str


class GenerateResponse(BaseModel):
    period: str
    created: list[InvoiceResponse]
    already_invoiced: int  # clients whose invoice for the month existed: left untouched
    skipped: list[SkippedClient]


class SettleBody(BaseModel):
    status: InvoiceStatus  # PAID or FOR_FREE


def _response(invoice: Invoice, now: datetime) -> InvoiceResponse:
    return InvoiceResponse(
        invoice_id=invoice.invoice_id,
        number=invoice.number,
        client_id=invoice.client_id,
        period=invoice.period,
        currency=invoice.currency,
        amount=format(invoice.amount, "f"),
        status=invoice.status,
        lines=[
            InvoiceLineResponse(
                plan_id=line.plan_id,
                plan_name=line.plan_name,
                monthly_price=format(line.monthly_price, "f"),
                currency=line.currency,
                included_credits=line.included_credits,
                daily_quota=line.daily_quota,
                days=line.days,
                days_in_period=line.days_in_period,
                amount=format(line.amount, "f"),
            )
            for line in invoice.lines
        ],
        created_at=invoice.created_at,
        created_by=invoice.created_by,
        status_changed_at=invoice.status_changed_at,
        status_changed_by=invoice.status_changed_by,
        payment_origin=invoice.payment_origin.value if invoice.payment_origin else None,
        payment_transaction_id=invoice.payment_transaction_id,
        payment_event_id=invoice.payment_event_id,
        due_at=invoice.due_at,
        overdue=invoice.is_overdue(now),
    )


def _event_response(record: PaymentRecord) -> PaymentEventResponse:
    return PaymentEventResponse(
        event_id=record.event_id,
        source=record.source,
        invoice_id=record.invoice_id,
        client_id=record.client_id,
        invoice_reference=record.invoice_reference,
        amount=format(record.amount, "f") if record.amount is not None else None,
        currency=record.currency,
        transaction_id=record.transaction_id,
        event_status=record.event_status,
        outcome=record.outcome,
        actor=record.actor,
        received_at=record.received_at,
    )


def create_admin_billing_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1/billing", tags=["admin"])

    def invoices():
        if application.invoices is None or application.plans is None:
            raise HTTPException(503, "billing needs the database (set SALES_AGENT_DATABASE_URL)")
        return application.invoices

    def period_param(value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parse_period(value)
        except InvalidPeriodError as error:
            raise HTTPException(422, str(error)) from None
        return value

    @router.get("/invoices", response_model=InvoicesResponse)
    def list_invoices(
        client_id: str | None = None,
        period: str | None = Query(default=None, description="One month, YYYY-MM"),
        period_from: str | None = None,
        period_to: str | None = None,
        status: InvoiceStatus | None = None,
        admin: AdminUser = Depends(get_current_admin),
    ) -> InvoicesResponse:
        store = invoices()
        period, period_from, period_to = (
            period_param(period),
            period_param(period_from),
            period_param(period_to),
        )
        where = InvoiceFilter(
            client_id=client_id,
            period_from=period or period_from,
            period_to=period or period_to,
            status=status,
        )
        try:
            found = store.list_invoices(where)
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        return InvoicesResponse(
            invoices=[
                _response(i, application.clock.now()) for i in found if admin.can_reach(i.client_id)
            ]
        )

    @router.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
    def get_invoice(
        invoice_id: int, admin: AdminUser = Depends(get_current_admin)
    ) -> InvoiceResponse:
        store = invoices()
        try:
            invoice = store.get(invoice_id)
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        if invoice is None or not admin.can_reach(invoice.client_id):
            raise HTTPException(404, "no such invoice")  # not even a hint that it exists
        return _response(invoice, application.clock.now())

    @router.get("/payment-events", response_model=PaymentEventsResponse)
    def payment_events(
        invoice_id: int | None = None, admin: AdminUser = Depends(get_current_admin)
    ) -> PaymentEventsResponse:
        """Every traced payment decision (webhook events, rejected ones included, and manual
        settlements), newest first, for the clients this admin can reach. Events whose invoice is
        unknown belong to no client and are shown to owners only."""
        store = invoices()
        try:
            found = store.payment_events(invoice_id)
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        visible = [
            r
            for r in found
            if (admin.can_reach(r.client_id) if r.client_id else admin.role is AdminRole.OWNER)
        ]
        return PaymentEventsResponse(events=[_event_response(r) for r in visible])

    @router.post("/invoices/generate", response_model=GenerateResponse)
    async def generate(
        body: GenerateBody, admin: AdminUser = Depends(require_owner)
    ) -> GenerateResponse:
        store = invoices()
        plans = application.plans
        assert plans is not None
        now = application.clock.now()
        try:
            start, end = ensure_closed(body.period, now)
        except InvalidPeriodError as error:
            raise HTTPException(422, str(error)) from None

        def run() -> GenerateResponse:
            assignments = plans.assignments_between(start, end)
            created, skipped, existing = [], [], 0
            for client_id in sorted({a.client_id for a in assignments}):
                try:
                    invoice = build_invoice(
                        client_id,
                        body.period,
                        assignments,
                        now=now,
                        created_by=admin.username,
                    )
                except MixedCurrenciesError:
                    skipped.append(
                        SkippedClient(
                            client_id=client_id,
                            reason="plans in different currencies during the month",
                        )
                    )
                    continue
                if invoice is None:
                    continue
                kept = store.create(invoice)
                if kept is None:
                    existing += 1
                else:
                    created.append(_response(kept, now))
            return GenerateResponse(
                period=body.period, created=created, already_invoiced=existing, skipped=skipped
            )

        try:
            return await run_in_threadpool(run)
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None

    @router.put("/invoices/{invoice_id}/status", response_model=InvoiceResponse)
    async def settle(
        invoice_id: int, body: SettleBody, admin: AdminUser = Depends(require_owner)
    ) -> InvoiceResponse:
        store = invoices()
        try:
            settled = await run_in_threadpool(
                lambda: store.settle(
                    invoice_id, body.status, at=application.clock.now(), by=admin.username
                )
            )
        except InvoiceNotFoundError:
            raise HTTPException(404, "no such invoice") from None
        except InvoiceStatusError as error:
            raise HTTPException(409, str(error)) from None
        except StoreUnavailableError as error:
            raise HTTPException(503, str(error)) from None
        return _response(settled, application.clock.now())

    return router
