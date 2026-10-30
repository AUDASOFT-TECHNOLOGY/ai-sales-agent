"""Admin API: the texts visitors and clients read, and the offline plan payments to validate.

- `GET /admin/v1/site-pages`, `PUT /admin/v1/site-pages/{key}/{locale}`: Contact, Privacy Policy, Terms
  of Service and the bank details, one text per language. An empty body removes that text (the page is
  then not published in that language). Owners write; every role reads.
- `GET /admin/v1/payment-requests[?status=]`, `GET .../{id}/receipt`, `POST .../{id}/approve|reject`:
  owners only. Approving puts the client on the plan (the existing assignment), issues the plan's
  invoice and marks it paid by hand (a bank transfer, declared by this Admin); the client is told.
  Rejecting needs a reason the client reads.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from sales_app.admin_auth import AdminRole, AdminUser
from sales_app.api.admin import make_current_admin_dependency, require_role
from sales_app.api.admin_billing import issue_after_assignment
from sales_app.billing import InvoiceFilter, InvoiceStatus
from sales_app.bootstrap import Application
from sales_app.notifications import CLIENT
from sales_app.plan_payments import (
    APPROVED,
    REJECTED,
    STATUSES,
    RequestNotFoundError,
    RequestNotPendingError,
)
from sales_app.plans import PlanInactiveError, PlanNotFoundError
from sales_app.site_content import BODY_MAX, KEYS, LOCALES, TITLE_MAX, SitePage
from sales_app.sourcing.errors import StoreUnavailableError

log = logging.getLogger(__name__)
UNAVAILABLE = "this is temporarily unavailable"


class SitePageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=TITLE_MAX)
    body: str = Field(default="", max_length=BODY_MAX)


class SitePageView(BaseModel):
    key: str
    locale: str
    title: str
    body: str
    updated_at: datetime
    updated_by: str


class SitePagesResponse(BaseModel):
    pages: list[SitePageView]


class AdminPaymentRequest(BaseModel):
    request_id: int
    client_id: str
    client_name: str
    plan_id: str
    plan_name: str
    amount: str
    currency: str
    reference: str
    receipt_type: str
    receipt_size: int
    status: str
    created_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    reason: str | None


class AdminPaymentRequests(BaseModel):
    requests: list[AdminPaymentRequest]


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=300)


def create_admin_site_router(application: Application) -> APIRouter:
    get_current_admin = make_current_admin_dependency(application.admin_auth)
    require_owner = require_role(get_current_admin, AdminRole.OWNER)
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    def pages():
        if application.site_pages is None:
            raise HTTPException(503, "this needs the database (set SALES_AGENT_DATABASE_URL)")
        return application.site_pages

    def payments():
        if application.plan_payments is None or application.plans is None:
            raise HTTPException(503, "this needs the database (set SALES_AGENT_DATABASE_URL)")
        return application.plan_payments

    # ---- texts -------------------------------------------------------------------------------

    @router.get("/site-pages", response_model=SitePagesResponse)
    def list_pages(_admin: AdminUser = Depends(get_current_admin)) -> SitePagesResponse:
        try:
            found = pages().list_all()
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None
        return SitePagesResponse(
            pages=[
                SitePageView(
                    key=p.key,
                    locale=p.locale,
                    title=p.title,
                    body=p.body,
                    updated_at=p.updated_at,
                    updated_by=p.updated_by,
                )
                for p in found
            ]
        )

    @router.put("/site-pages/{key}/{locale}", response_model=SitePagesResponse)
    def save_page(
        key: str, locale: str, body: SitePageBody, admin: AdminUser = Depends(require_owner)
    ) -> SitePagesResponse:
        if key not in KEYS or locale not in LOCALES:
            raise HTTPException(404, "no such page")
        store = pages()
        title, text = body.title.strip(), body.body.strip()
        try:
            if not text:
                store.remove(key, locale)
            else:
                if not title:
                    raise HTTPException(400, "a page needs a title")
                store.save(
                    SitePage(key, locale, title, text, application.clock.now(), admin.username)
                )
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None
        return list_pages(admin)

    # ---- offline payments --------------------------------------------------------------------

    def client_name(client_id: str) -> str:
        record = application.client_records.get(client_id) if application.client_records else None
        return record.display_name if record is not None else client_id

    def admin_view(r) -> AdminPaymentRequest:
        return AdminPaymentRequest(
            request_id=r.request_id or 0,
            client_id=r.client_id,
            client_name=client_name(r.client_id),
            plan_id=r.plan_id,
            plan_name=r.plan_name,
            amount=format(r.amount, "f"),
            currency=r.currency,
            reference=r.reference,
            receipt_type=r.receipt_type,
            receipt_size=r.receipt_size,
            status=r.status,
            created_at=r.created_at,
            decided_at=r.decided_at,
            decided_by=r.decided_by,
            reason=r.reason,
        )

    @router.get("/payment-requests", response_model=AdminPaymentRequests)
    def list_requests(
        status: str | None = None, _admin: AdminUser = Depends(require_owner)
    ) -> AdminPaymentRequests:
        if status is not None and status not in STATUSES:
            raise HTTPException(422, "unknown status")
        try:
            found = payments().list_requests(status=status)
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None
        return AdminPaymentRequests(requests=[admin_view(r) for r in found])

    @router.get("/payment-requests/{request_id}/receipt")
    def receipt(request_id: int, _admin: AdminUser = Depends(require_owner)) -> Response:
        try:
            found = payments().receipt(request_id)
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None
        if found is None:
            raise HTTPException(404, "no such receipt")
        return Response(
            found.data,
            media_type=found.content_type,
            headers={
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "sandbox",
            },
        )

    def tell_client(client_id: str, kind: str, title: str, message: str, key: str) -> None:
        application.notifications.notify(
            kind, CLIENT, client_id, title=title, message=message, action="", dedupe_key=key
        )

    @router.post("/payment-requests/{request_id}/approve", response_model=AdminPaymentRequest)
    def approve(request_id: int, admin: AdminUser = Depends(require_owner)) -> AdminPaymentRequest:
        store = payments()
        now = application.clock.now()
        by = f"admin:{admin.username}"
        try:
            current = store.get(request_id)
            if current is None:
                raise HTTPException(404, "no such request")
            plan = application.plans.get_plan(current.plan_id)  # type: ignore[union-attr]
            if plan is None or not plan.active:
                raise HTTPException(409, "this plan is not available any more: reject the request")
            decided = store.decide(request_id, APPROVED, at=now, by=admin.username, reason=None)
        except RequestNotFoundError:
            raise HTTPException(404, "no such request") from None
        except RequestNotPendingError:
            raise HTTPException(409, "this request was already decided") from None
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None
        try:  # the decision is made first (once); if the plan cannot be given, it is undone
            application.plans.assign(decided.client_id, decided.plan_id, at=now, by=by)  # type: ignore[union-attr]
        except (StoreUnavailableError, PlanNotFoundError, PlanInactiveError):
            store.reopen(request_id)
            raise HTTPException(
                503, "the plan could not be assigned: nothing was changed"
            ) from None
        issue_after_assignment(application, decided.client_id, by)
        settle_invoice(decided.client_id, now, admin.username)
        tell_client(
            decided.client_id,
            "plan_activated",
            "Your plan is active",
            f"Your payment was confirmed: the {decided.plan_name} plan is active. You can start your agent.",
            f"plan_activated:{request_id}",
        )
        return admin_view(decided)

    def settle_invoice(client_id: str, now: datetime, username: str) -> None:
        """The plan's invoice, just issued, is paid by the transfer the Admin checked."""
        invoices = application.invoices
        if invoices is None:
            return
        try:
            for invoice in invoices.list_invoices(
                InvoiceFilter(client_id=client_id, status=InvoiceStatus.PENDING)
            ):
                invoices.settle(
                    invoice.invoice_id, InvoiceStatus.PAID, at=now, by=username, reason=None
                )
                return
        except Exception:  # noqa: BLE001 - the plan is active; the invoice can be settled by hand
            log.warning("could not mark the plan invoice of %s as paid", client_id)

    @router.post("/payment-requests/{request_id}/reject", response_model=AdminPaymentRequest)
    def reject(
        request_id: int, body: RejectBody, admin: AdminUser = Depends(require_owner)
    ) -> AdminPaymentRequest:
        store = payments()
        try:
            decided = store.decide(
                request_id,
                REJECTED,
                at=application.clock.now(),
                by=admin.username,
                reason=body.reason.strip(),
            )
        except RequestNotFoundError:
            raise HTTPException(404, "no such request") from None
        except RequestNotPendingError:
            raise HTTPException(409, "this request was already decided") from None
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None
        tell_client(
            decided.client_id,
            "payment_rejected",
            "Your payment was not accepted",
            f"{decided.reason} You can send a new receipt.",
            f"payment_rejected:{request_id}",
        )
        return admin_view(decided)

    return router
