"""Admin API for invoices, one per billing cycle (see `sales_app.billing`).

Any admin reads the invoices of the clients they can reach (a client outside their scope does not
appear, and its invoice is a 404). Only an owner generates invoices or settles them. Invoices are issued
automatically (on a plan assignment, then every month on the same day; `BillingLoop`); "generate"
issues, now, every cycle that is due and not invoiced yet, at the plan's price: an invoice that
already exists is never rebuilt or duplicated. Marking an invoice PAID,
FOR_FREE or DISCOUNTED_100 (a 100% discount: billed amount kept, amount after discount 0, amounts
discounted and free recorded, who and when traced, optional reason) is the owner's declaration, once (origin `manual`, traced); an invoice already paid by a
verified electronic confirmation (`POST /api/payments/webhook`, see `sales_app.payment_confirmation`)
refuses it with a 409. `overdue` is computed: unpaid (PENDING/FAILED) `DUE_DAYS` after the invoice date.

Amounts travel as decimal strings, never floats.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import make_current_admin_dependency, require_role
from sales_app.billing import (
    InvalidPeriodError,
    Invoice,
    InvoiceFilter,
    InvoiceNotFoundError,
    InvoiceStatus,
    InvoiceStatusError,
    PaymentRecord,
    parse_period,
)
from sales_app.billing_service import AutoBilling
from sales_app.bootstrap import Application
from sales_app.sourcing.errors import StoreUnavailableError


class InvoiceLineResponse(BaseModel):
    plan_id: str
    plan_name: str
    monthly_price: str
    currency: str
    included_credits: int
    daily_quota: int
    amount: str


class InvoiceResponse(BaseModel):
    invoice_id: int
    number: str
    client_id: str
    period: str  # the cycle date YYYY-MM-DD (legacy invoices: the month, YYYY-MM)
    issued_on: date  # the invoice date
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
    discount_percent: int  # 0 unless discounted
    amount_after_discount: str | None  # `amount` is the billed amount before any discount
    discount_amount: str | None
    free_amount: str | None  # given free (the whole amount for FOR_FREE and DISCOUNTED_100)
    discount_reason: str | None
    collected: bool  # money received: only PAID; FOR_FREE and a discount never are
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
    """Nothing is needed: the invoices due now are issued. `period` is accepted and ignored (the
    former monthly form), so older callers keep working."""

    period: str | None = None


class SkippedClient(BaseModel):
    client_id: str
    reason: str


class GenerateResponse(BaseModel):
    as_of: datetime  # the invoices due at this moment were issued
    created: list[InvoiceResponse]
    already_invoiced: int  # due cycles whose invoice existed: left untouched
    skipped: list[SkippedClient]


class SettleBody(BaseModel):
    status: InvoiceStatus  # PAID, FOR_FREE or DISCOUNTED_100
    reason: str | None = Field(default=None, max_length=255)  # kept with a discount


def _money(value) -> str | None:
    return None if value is None else format(value, "f")


def _response(invoice: Invoice, now: datetime) -> InvoiceResponse:
    return InvoiceResponse(
        invoice_id=invoice.invoice_id,
        number=invoice.number,
        client_id=invoice.client_id,
        period=invoice.period,
        issued_on=invoice.issued_on,
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
        discount_percent=invoice.discount_percent,
        amount_after_discount=_money(invoice.amount_after_discount),
        discount_amount=_money(invoice.discount_amount),
        free_amount=_money(invoice.free_total),
        discount_reason=invoice.discount_reason,
        collected=invoice.is_collected,
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


def auto_billing_of(application: Application) -> AutoBilling | None:
    """The invoicing service over the application's current plan and invoice stores (None without
    them)."""
    if application.plans is None or application.invoices is None:
        return None
    return AutoBilling(
        application.plans, application.invoices, application.notifications, application.audit
    )


def issue_after_assignment(application: Application, client_id: str, by: str) -> None:
    """Right after a plan assignment: the invoice of that day, at the new plan's price. A failure
    never undoes the assignment (it is reported and the periodic run retries)."""
    billing = auto_billing_of(application)
    if billing is not None:
        billing.issue_for_client(client_id, application.clock.now(), created_by=by)


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
        period: str | None = Query(default=None, description="One cycle date YYYY-MM-DD, or one month YYYY-MM"),
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
        body: GenerateBody | None = None, admin: AdminUser = Depends(require_owner)
    ) -> GenerateResponse:
        """Issues, now, every invoice that is due and not issued yet (idempotent)."""
        invoices()
        billing = auto_billing_of(application)
        if billing is None:
            raise HTTPException(503, "billing needs the database (set SALES_AGENT_DATABASE_URL)")
        now = application.clock.now()
        report = await run_in_threadpool(lambda: billing.run(now, created_by=admin.username))
        if report.errors and not report.invoices and not report.already_invoiced:
            raise HTTPException(503, "the invoices are unavailable")
        return GenerateResponse(
            as_of=now,
            created=[_response(i, now) for i in report.invoices],
            already_invoiced=report.already_invoiced,
            skipped=[SkippedClient(client_id=k.client_id, reason=k.reason) for k in report.skipped],
        )

    @router.put("/invoices/{invoice_id}/status", response_model=InvoiceResponse)
    async def settle(
        invoice_id: int, body: SettleBody, admin: AdminUser = Depends(require_owner)
    ) -> InvoiceResponse:
        store = invoices()
        try:
            settled = await run_in_threadpool(
                lambda: store.settle(
                    invoice_id,
                    body.status,
                    at=application.clock.now(),
                    by=admin.username,
                    reason=body.reason,
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
