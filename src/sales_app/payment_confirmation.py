"""Reliable confirmation of invoice payments.

A payment is confirmed only by a notification that a `PaymentProvider` proved authentic (signature
over the raw body). The notification is turned into a `PaymentEvent` and applied by the invoice store
in one atomic, idempotent step (`InvoiceStore.process_payment`):

* `succeeded`, exact amount and currency of the invoice, unknown transaction -> `PAID`, origin
  `electronic`, with the transaction id and event id. The same event id, or the same transaction id,
  again is a no-op (`duplicate`).
* amount or currency different, invoice unknown, transaction already used by another invoice,
  invoice already settled -> rejected: nothing changes except a trace row in `payment_events`
  (the Admin can read them, `GET /admin/v1/billing/payment-events`).
* `failed` -> `FAILED` (a later success, or the Admin, can still pay it); an invoice already
  settled stays as it is.

The provider is resolved on every call (`resolver`), so a secret the Admin sets or changes applies
at once. No provider configured -> `NoPaymentProviderError`.
"""

from collections.abc import Callable
from datetime import datetime

from sales_app.billing import InvoiceStore, PaymentResult
from sales_app.payment_provider import PaymentProvider

PAYMENT_WEBHOOK_SECRET = (
    "payments.webhook_secret"  # global credential key (see secret_store.catalog)
)

ProviderResolver = Callable[[], PaymentProvider | None]


class NoPaymentProviderError(Exception):
    """No payment provider is configured."""


def confirm_payment(
    resolver: ProviderResolver | None,
    invoices: InvoiceStore,
    body: bytes,
    headers: dict[str, str],
    *,
    now: datetime,
) -> PaymentResult:
    """Verify, normalise and apply one webhook. Raises `NoPaymentProviderError`,
    `payment_provider.InvalidSignatureError` or `MalformedPaymentEventError`."""
    provider = resolver() if resolver is not None else None
    if provider is None:
        raise NoPaymentProviderError
    event = provider.parse_webhook(body, headers, now=now)
    return invoices.process_payment(event, at=now, by=f"payment:{provider.name}")
