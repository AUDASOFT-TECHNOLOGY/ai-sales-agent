"""The port to whatever tells Revansa that an invoice was paid, and a generic signed-webhook adapter.

Revansa never collects money itself and has no integration with a named payment company. A provider
only has to do two things: prove a notification is authentic, and say what it means in one
normalised shape (`PaymentEvent`). The application (`sales_app.payment_confirmation`) does the rest.

`SignedWebhookProvider` is a real, generic adapter for any provider (or middleman) that can POST a
JSON body signed with a shared secret:

* headers `X-Payment-Timestamp` (unix seconds) and `X-Payment-Signature` (hex, lowercase);
* signature = HMAC-SHA256(secret, `<timestamp>.<raw body>`), compared in constant time;
* a timestamp further than `tolerance` seconds from now is refused (replay window; the event id is
  the real idempotency key);
* body = JSON object `{"event_id", "invoice", "amount", "currency", "transaction_id", "status"}`
  where `invoice` is the invoice number (`INV-2026-04-alpha`), `amount` a decimal string or integer
  with at most two decimals, and `status` is `succeeded` or `failed`.

The secret is the global credential `payments.webhook_secret` (set by the Admin; never returned).
"""

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol

SIGNATURE_HEADER = "x-payment-signature"
TIMESTAMP_HEADER = "x-payment-timestamp"
MAX_BODY_BYTES = 65536

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REFERENCE = re.compile(r"^INV-\d{4}-(0[1-9]|1[0-2])-.{1,64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class PaymentStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class InvalidSignatureError(Exception):
    """The notification is not proven authentic. Never says why (no oracle for an attacker)."""


class MalformedPaymentEventError(ValueError):
    """Authentic but not a payment event this system understands."""


@dataclass(frozen=True, slots=True)
class PaymentEvent:
    event_id: str  # the provider's unique id of this notification: the idempotency key
    invoice_reference: str  # the invoice number
    amount: Decimal
    currency: str
    transaction_id: str
    status: PaymentStatus


class PaymentProvider(Protocol):
    name: str

    def parse_webhook(
        self, body: bytes, headers: Mapping[str, str], *, now: datetime
    ) -> PaymentEvent:
        """Verifies the raw body, then normalises it. `InvalidSignatureError` if it is not proven
        authentic; `MalformedPaymentEventError` if it is authentic but unusable."""
        ...


def parse_event(body: bytes) -> PaymentEvent:
    """The normalised event of a signed-webhook JSON body (verification is the caller's job)."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise MalformedPaymentEventError("the body is not JSON") from None
    if not isinstance(data, dict):
        raise MalformedPaymentEventError("the body must be a JSON object")

    def text(key: str, pattern: re.Pattern[str]) -> str:
        value = data.get(key)
        if not isinstance(value, str) or pattern.match(value) is None:
            raise MalformedPaymentEventError(f"invalid {key}")
        return value

    raw_amount = data.get("amount")
    if isinstance(raw_amount, bool) or not isinstance(raw_amount, (str, int)):
        raise MalformedPaymentEventError("invalid amount")
    try:
        amount = Decimal(str(raw_amount).strip())
    except InvalidOperation:
        raise MalformedPaymentEventError("invalid amount") from None
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
        raise MalformedPaymentEventError("invalid amount")
    try:
        status = PaymentStatus(data.get("status"))
    except ValueError:
        raise MalformedPaymentEventError("invalid status") from None
    return PaymentEvent(
        event_id=text("event_id", _ID),
        invoice_reference=text("invoice", _REFERENCE),
        amount=amount.quantize(Decimal("0.01")),
        currency=text("currency", _CURRENCY),
        transaction_id=text("transaction_id", _ID),
        status=status,
    )


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return ""


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """The signature a sender computes (also what the tests and any integrator use)."""
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


class SignedWebhookProvider:
    name = "signed-webhook"

    def __init__(self, secret: str, *, tolerance_seconds: int = 300) -> None:
        if not secret:
            raise ValueError("a signing secret is required")
        self._secret = secret
        self._tolerance = tolerance_seconds

    def parse_webhook(
        self, body: bytes, headers: Mapping[str, str], *, now: datetime
    ) -> PaymentEvent:
        timestamp = _header(headers, TIMESTAMP_HEADER).strip()
        given = _header(headers, SIGNATURE_HEADER).strip().lower()
        if not timestamp.isdigit() or len(timestamp) > 12 or not given:
            raise InvalidSignatureError
        expected = sign(self._secret, timestamp, body)
        # compare bytes, in constant time (str with non-ASCII would raise instead)
        if not hmac.compare_digest(given.encode("utf-8"), expected.encode()):
            raise InvalidSignatureError
        if abs(now.timestamp() - int(timestamp)) > self._tolerance:
            raise InvalidSignatureError
        return parse_event(body)


class FakePaymentProvider:
    """For tests: authentic iff the header `x-fake-signature` is `valid`; same JSON body."""

    name = "fake"

    def parse_webhook(
        self, body: bytes, headers: Mapping[str, str], *, now: datetime
    ) -> PaymentEvent:
        if _header(headers, "x-fake-signature") != "valid":
            raise InvalidSignatureError
        return parse_event(body)
