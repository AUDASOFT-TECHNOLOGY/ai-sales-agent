"""Public payment-confirmation webhook (`POST /api/payments/webhook`).

No Admin session: the caller is the payment provider, authenticated only by the signature over the
raw body (see `sales_app.payment_provider`). Answers:

* 409 `no payment provider configured` -- the Admin has not set the signing secret;
* 401 -- missing, wrong or stale signature (nothing else is said);
* 400 -- authentic but not a payment event, or a body that is too large;
* 200 `{"status": "applied" | "duplicate" | "failed_recorded" | "rejected"}` -- the event was
  processed. A rejection (amount mismatch, unknown invoice...) is final and traced for the Admin, so
  it is answered 200: a retry could not change it. Nothing about invoices or clients is revealed.
* 503 -- the database is unavailable: the provider should retry (the event id keeps it idempotent).
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from sales_app.billing import PaymentOutcome
from sales_app.bootstrap import Application
from sales_app.payment_confirmation import NoPaymentProviderError, confirm_payment
from sales_app.payment_provider import (
    MAX_BODY_BYTES,
    InvalidSignatureError,
    MalformedPaymentEventError,
)
from sales_app.sourcing.errors import StoreUnavailableError

log = logging.getLogger(__name__)

_PUBLIC = {
    PaymentOutcome.APPLIED: "applied",
    PaymentOutcome.DUPLICATE: "duplicate",
    PaymentOutcome.FAILURE_RECORDED: "failed_recorded",
}


def create_payments_router(application: Application) -> APIRouter:
    router = APIRouter(prefix="/api/payments", tags=["payments"])

    @router.post("/webhook")
    async def webhook(request: Request) -> dict[str, str]:
        if application.payment_provider is None or application.invoices is None:
            raise HTTPException(409, "no payment provider configured")
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(400, "invalid payment notification")
        store = application.invoices
        resolver = application.payment_provider
        headers = dict(request.headers)
        now = application.clock.now()
        try:
            result = await run_in_threadpool(
                lambda: confirm_payment(resolver, store, body, headers, now=now)
            )
        except NoPaymentProviderError:
            raise HTTPException(409, "no payment provider configured") from None
        except InvalidSignatureError:
            log.warning("rejected payment webhook: invalid signature")
            raise HTTPException(401, "invalid signature") from None
        except MalformedPaymentEventError:
            raise HTTPException(400, "invalid payment notification") from None
        except StoreUnavailableError:
            raise HTTPException(503, "temporarily unavailable") from None
        if result.outcome not in _PUBLIC:
            log.warning("payment event rejected: %s", result.outcome.value)
        return {"status": _PUBLIC.get(result.outcome, "rejected")}

    return router
