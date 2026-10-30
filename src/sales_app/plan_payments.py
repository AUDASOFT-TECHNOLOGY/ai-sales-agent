"""Offline payment of a plan: the client pays by bank transfer and sends the receipt; the Admin checks it
and, if it is right, approves the request, which puts the client on the plan.

Nothing here charges anyone. A request is `pending` until the Admin decides (`approved` or `rejected`);
a client has at most one pending request. The receipt (a PDF or an image) is kept with the request and
is read only by that client's Admin side, never listed with the request.
"""

import threading
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Protocol

PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
STATUSES = (PENDING, APPROVED, REJECTED)
MAX_RECEIPT_BYTES = 3 * 1024 * 1024


class PendingRequestExistsError(Exception):
    """The client already has a request waiting for the Admin."""


class RequestNotFoundError(LookupError):
    """No such request."""


class RequestNotPendingError(Exception):
    """The request was already decided."""


@dataclass(frozen=True, slots=True)
class PaymentRequest:
    request_id: int | None
    client_id: str
    plan_id: str
    plan_name: str
    amount: Decimal
    currency: str
    reference: str
    receipt_type: str
    receipt_size: int
    status: str
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Receipt:
    content_type: str
    data: bytes


class PlanPaymentStore(Protocol):
    def create(self, request: PaymentRequest, receipt: Receipt) -> PaymentRequest:
        """`PendingRequestExistsError` if the client already has one waiting."""

    def get(self, request_id: int) -> PaymentRequest | None: ...

    def list_requests(
        self, *, client_id: str | None = None, status: str | None = None
    ) -> list[PaymentRequest]:
        """Newest first."""

    def receipt(self, request_id: int) -> Receipt | None: ...

    def decide(
        self, request_id: int, status: str, *, at: datetime, by: str, reason: str | None
    ) -> PaymentRequest:
        """pending -> approved/rejected, once. `RequestNotFoundError`, `RequestNotPendingError`."""

    def reopen(self, request_id: int) -> None:
        """Puts a decided request back to pending: only to undo a decision whose effect failed."""


class InMemoryPlanPaymentStore:
    def __init__(self) -> None:
        self._requests: dict[int, PaymentRequest] = {}
        self._receipts: dict[int, Receipt] = {}
        self._next = 1
        self._lock = threading.Lock()

    def create(self, request: PaymentRequest, receipt: Receipt) -> PaymentRequest:
        with self._lock:
            if any(
                r.client_id == request.client_id and r.status == PENDING
                for r in self._requests.values()
            ):
                raise PendingRequestExistsError(request.client_id)
            made = replace(request, request_id=self._next)
            self._requests[self._next] = made
            self._receipts[self._next] = receipt
            self._next += 1
            return made

    def get(self, request_id: int) -> PaymentRequest | None:
        with self._lock:
            return self._requests.get(request_id)

    def list_requests(
        self, *, client_id: str | None = None, status: str | None = None
    ) -> list[PaymentRequest]:
        with self._lock:
            found = [
                r
                for r in self._requests.values()
                if (client_id is None or r.client_id == client_id)
                and (status is None or r.status == status)
            ]
        return sorted(found, key=lambda r: r.request_id or 0, reverse=True)

    def receipt(self, request_id: int) -> Receipt | None:
        with self._lock:
            return self._receipts.get(request_id)

    def decide(
        self, request_id: int, status: str, *, at: datetime, by: str, reason: str | None
    ) -> PaymentRequest:
        with self._lock:
            current = self._requests.get(request_id)
            if current is None:
                raise RequestNotFoundError(request_id)
            if current.status != PENDING:
                raise RequestNotPendingError(request_id)
            decided = replace(current, status=status, decided_at=at, decided_by=by, reason=reason)
            self._requests[request_id] = decided
            return decided

    def reopen(self, request_id: int) -> None:
        with self._lock:
            current = self._requests.get(request_id)
            if current is not None:
                self._requests[request_id] = replace(
                    current, status=PENDING, decided_at=None, decided_by=None, reason=None
                )
