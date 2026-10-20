"""Client API: the client's own invoices and subscription, read only (spec 13.2).

- `GET /client/v1/invoices`: this client's invoices, newest period first.
- `GET /client/v1/invoices/{invoice_id}`: one of them, with its lines. Another client's invoice, and
  an unknown one, are the same 404.
- `GET /client/v1/subscription`: the plan the client is on, its periodicity, the status of the
  subscription, the next billing date, the plan's quotas and where the last invoice stands.

None takes a client id: the session decides it (the store is always asked for that client only), so
no id sent by the browser can reach another client's invoices. Nothing here changes an invoice or its
status: declaring a payment, a free invoice or a discount stays with the Admin, and an electronic
payment arrives through the verified webhook. Internal traces (who settled it, the provider's
transaction and event ids) are not shown. Amounts are decimal strings. Every answer is
`Cache-Control: no-store`.
"""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sales_app.api.client import make_current_client_dependency
from sales_app.billing import Invoice, InvoiceFilter, InvoiceStatus, parse_period
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.sourcing.errors import StoreUnavailableError

UNAVAILABLE = "billing is temporarily unavailable"


class ClientInvoiceLine(BaseModel):
    plan_name: str
    monthly_price: str
    currency: str
    days: int
    days_in_period: int
    amount: str


class ClientInvoice(BaseModel):
    invoice_id: int
    number: str
    period: str
    currency: str
    amount: str  # billed, before any discount
    amount_after_discount: str | None
    discount_amount: str | None
    free_amount: str | None
    status: InvoiceStatus
    collected: bool  # money received: only PAID
    due_at: datetime
    overdue: bool
    created_at: datetime
    status_changed_at: datetime | None


class ClientInvoiceDetail(ClientInvoice):
    lines: list[ClientInvoiceLine]


class ClientInvoices(BaseModel):
    invoices: list[ClientInvoice]


class SubscriptionPlan(BaseModel):
    plan_id: str
    name: str
    monthly_price: str
    currency: str
    billing_period: str  # "monthly" | "annual"
    included_credits: int
    daily_quota: int
    since: datetime


class LastPayment(BaseModel):
    invoice_number: str
    period: str
    status: InvoiceStatus
    collected: bool
    settled_at: datetime | None


class Subscription(BaseModel):
    status: str  # "active" (on a plan) | "no_plan"
    plan: SubscriptionPlan | None
    next_billing_at: datetime | None  # when the current month ends: its invoice is made then
    unpaid_invoices: int
    overdue_invoices: int
    last_payment: LastPayment | None  # the most recent invoice and how it stands


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _invoice(invoice: Invoice, now: datetime) -> dict:
    return {
        "invoice_id": invoice.invoice_id,
        "number": invoice.number,
        "period": invoice.period,
        "currency": invoice.currency,
        "amount": format(invoice.amount, "f"),
        "amount_after_discount": _money(invoice.amount_after_discount),
        "discount_amount": _money(invoice.discount_amount),
        "free_amount": _money(invoice.free_total),
        "status": invoice.status,
        "collected": invoice.is_collected,
        "due_at": invoice.due_at,
        "overdue": invoice.is_overdue(now),
        "created_at": invoice.created_at,
        "status_changed_at": invoice.status_changed_at,
    }


def create_client_billing_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1", tags=["client"])

    def stores():
        if application.invoices is None or application.plans is None:
            raise HTTPException(503, UNAVAILABLE)
        return application.invoices, application.plans

    @router.get("/invoices", response_model=ClientInvoices)
    async def list_invoices(
        response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> ClientInvoices:
        response.headers["Cache-Control"] = "no-store"
        invoices, _ = stores()
        try:
            found = await run_in_threadpool(
                invoices.list_invoices, InvoiceFilter(client_id=client.client_id)
            )
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None
        now = application.clock.now()
        return ClientInvoices(
            invoices=[
                ClientInvoice(**_invoice(i, now)) for i in found if i.client_id == client.client_id
            ]
        )

    @router.get("/invoices/{invoice_id}", response_model=ClientInvoiceDetail)
    async def get_invoice(
        invoice_id: int, response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> ClientInvoiceDetail:
        response.headers["Cache-Control"] = "no-store"
        invoices, _ = stores()
        try:
            invoice = await run_in_threadpool(invoices.get, invoice_id)
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None
        if invoice is None or invoice.client_id != client.client_id:
            raise HTTPException(404, "no such invoice")  # not even a hint that it exists
        return ClientInvoiceDetail(
            **_invoice(invoice, application.clock.now()),
            lines=[
                ClientInvoiceLine(
                    plan_name=line.plan_name,
                    monthly_price=format(line.monthly_price, "f"),
                    currency=line.currency,
                    days=line.days,
                    days_in_period=line.days_in_period,
                    amount=format(line.amount, "f"),
                )
                for line in invoice.lines
            ],
        )

    @router.get("/subscription", response_model=Subscription)
    async def subscription(
        response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> Subscription:
        response.headers["Cache-Control"] = "no-store"
        invoices, plans = stores()
        now = application.clock.now()

        def read() -> Subscription:
            assignment = plans.current_assignment(client.client_id)
            mine = invoices.list_invoices(InvoiceFilter(client_id=client.client_id))
            plan = None
            if assignment is not None:
                current = plans.get_plan(assignment.plan_id)
                plan = SubscriptionPlan(
                    plan_id=assignment.plan_id,
                    name=assignment.plan_name,
                    monthly_price=format(assignment.monthly_price, "f"),
                    currency=assignment.currency,
                    billing_period=current.billing_period if current else "monthly",
                    included_credits=assignment.included_credits,
                    daily_quota=assignment.daily_quota,
                    since=assignment.started_at,
                )
            unpaid = [i for i in mine if i.status in (InvoiceStatus.PENDING, InvoiceStatus.FAILED)]
            last = mine[0] if mine else None  # newest period first
            this_month = f"{now.year:04d}-{now.month:02d}"
            return Subscription(
                status="active" if assignment is not None else "no_plan",
                plan=plan,
                next_billing_at=parse_period(this_month)[1] if assignment is not None else None,
                unpaid_invoices=len(unpaid),
                overdue_invoices=sum(1 for i in unpaid if i.is_overdue(now)),
                last_payment=None
                if last is None
                else LastPayment(
                    invoice_number=last.number,
                    period=last.period,
                    status=last.status,
                    collected=last.is_collected,
                    settled_at=last.status_changed_at,
                ),
            )

        try:
            return await run_in_threadpool(read)
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None

    return router
