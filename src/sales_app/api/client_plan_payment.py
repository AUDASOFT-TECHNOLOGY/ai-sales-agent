"""Client API: paying for a plan offline (bank transfer) and sending the receipt.

- `GET /client/v1/account/bank-details`: the bank details the Admin wrote (404 until they exist).
- `GET /client/v1/account/payment-requests`: this client's requests and where each stands.
- `POST /client/v1/account/payment-requests`: the chosen plan, an optional bank reference and the
  receipt (a PDF or an image, at most 3 MB, sent as a `data:` URL). The Admin is told; nothing is
  activated until the Admin approves (`admin_site`). One request waits at a time.

The caller is always the session's client. A receipt is never returned to the client: only the
Admin side reads it.
"""

import base64
import binascii
import re
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from sales_app.api.client import make_current_client_dependency
from sales_app.bootstrap import Application
from sales_app.client_profiles import ClientProfile
from sales_app.notifications import ADMIN, PLAN_REQUEST
from sales_app.plan_payments import (
    MAX_RECEIPT_BYTES,
    PENDING,
    PaymentRequest,
    PendingRequestExistsError,
    Receipt,
)
from sales_app.site_content import BANK, read_page
from sales_app.sourcing.errors import StoreUnavailableError

UNAVAILABLE = "payments are temporarily unavailable"
_DATA_URL = re.compile(
    r"^data:(application/pdf|image/png|image/jpeg|image/webp);base64,([A-Za-z0-9+/]+={0,2})$"
)
_MAGIC = {
    "application/pdf": b"%PDF",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/webp": b"RIFF",
}


def parse_receipt(value: str) -> Receipt:
    """A genuine small PDF or raster image, or a 400 saying what is wrong. SVG is refused on purpose
    (it can carry script)."""
    match = _DATA_URL.match(value)
    if match is None:
        raise HTTPException(400, "the receipt must be a PDF, PNG, JPEG or WebP file")
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except binascii.Error:
        raise HTTPException(400, "the receipt is not valid") from None
    if len(data) > MAX_RECEIPT_BYTES:
        raise HTTPException(
            400, f"the receipt must be at most {MAX_RECEIPT_BYTES // 1024 // 1024} MB"
        )
    kind = match.group(1)
    if not data.startswith(_MAGIC[kind]) or (kind == "image/webp" and data[8:12] != b"WEBP"):
        raise HTTPException(400, "the receipt content does not match its file type")
    return Receipt(kind, data)


class BankDetails(BaseModel):
    title: str
    body: str


class PaymentRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=64)
    reference: str = Field(default="", max_length=120)
    receipt: str = Field(min_length=20, max_length=4_400_000)


class PaymentRequestView(BaseModel):
    request_id: int
    plan_id: str
    plan_name: str
    amount: str
    currency: str
    reference: str
    status: str
    created_at: datetime
    decided_at: datetime | None
    reason: str | None


class PaymentRequestList(BaseModel):
    requests: list[PaymentRequestView]


def view(r: PaymentRequest) -> PaymentRequestView:
    return PaymentRequestView(
        request_id=r.request_id or 0,
        plan_id=r.plan_id,
        plan_name=r.plan_name,
        amount=format(r.amount, "f"),
        currency=r.currency,
        reference=r.reference,
        status=r.status,
        created_at=r.created_at,
        decided_at=r.decided_at,
        reason=r.reason,
    )


def create_client_plan_payment_router(application: Application) -> APIRouter:
    get_current_client = make_current_client_dependency(application.client_auth)
    router = APIRouter(prefix="/client/v1/account", tags=["client"])

    def payments():
        if application.plan_payments is None or application.plans is None:
            raise HTTPException(503, "payments need the database")
        return application.plan_payments

    @router.get("/bank-details", response_model=BankDetails)
    def bank_details(
        response: Response, locale: str = "en", _client: ClientProfile = Depends(get_current_client)
    ) -> BankDetails:
        response.headers["Cache-Control"] = "no-store"
        if application.site_pages is None:
            raise HTTPException(404, "no bank details yet")
        try:
            page = read_page(application.site_pages, BANK, locale)
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None
        if page is None:
            raise HTTPException(404, "no bank details yet")
        return BankDetails(title=page.title, body=page.body)

    @router.get("/payment-requests", response_model=PaymentRequestList)
    def list_requests(
        response: Response, client: ClientProfile = Depends(get_current_client)
    ) -> PaymentRequestList:
        response.headers["Cache-Control"] = "no-store"
        try:
            found = payments().list_requests(client_id=client.client_id)
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None
        return PaymentRequestList(requests=[view(r) for r in found])

    @router.post("/payment-requests", response_model=PaymentRequestView, status_code=201)
    def send_request(
        body: PaymentRequestBody, client: ClientProfile = Depends(get_current_client)
    ) -> PaymentRequestView:
        store = payments()
        receipt = parse_receipt(body.receipt)
        try:
            plan = application.plans.get_plan(body.plan_id)  # type: ignore[union-attr]
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None
        if plan is None or not plan.active:
            raise HTTPException(400, "this plan is not available")
        request = PaymentRequest(
            request_id=None,
            client_id=client.client_id,
            plan_id=plan.plan_id,
            plan_name=plan.name,
            amount=Decimal(plan.monthly_price),
            currency=plan.currency,
            reference=" ".join(body.reference.split()),
            receipt_type=receipt.content_type,
            receipt_size=len(receipt.data),
            status=PENDING,
            created_at=application.clock.now(),
        )
        try:
            made = store.create(request, receipt)
        except PendingRequestExistsError:
            raise HTTPException(409, "a payment request is already waiting for review") from None
        except StoreUnavailableError:
            raise HTTPException(503, UNAVAILABLE) from None
        application.notifications.notify(
            PLAN_REQUEST,
            ADMIN,
            client.client_id,
            title="A payment receipt is waiting for review",
            message=f"A client sent a receipt for the {plan.name} plan.",
            action="Open Payments, check the receipt and approve or reject it.",
            dedupe_key=f"plan_request:{made.request_id}",
        )
        return view(made)

    return router
