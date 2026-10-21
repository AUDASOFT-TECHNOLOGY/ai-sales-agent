"""Invoices: what each client owes per billing cycle, at the price of the plan it is on.

**Where the amounts come from.** An invoice is built from the client's current plan assignment
(`sales_app.plans`), which keeps the plan's terms as they were when the client was put on it. It has
one line (plan, price, currency) and its amount is that price.

**A history that does not move.** An invoice carries its own copy of every line (plan name, price,
currency, credits, quota, amount). Editing or deactivating a plan afterwards changes nothing
already invoiced, and issuing a cycle again never touches an invoice that exists (one invoice
per client and period, enforced by the database).

**When billing starts, and the cycles.** A client is billed from the day its plan was assigned
(`PlanAssignment.started_at`, set by `plans.assign`). That day is the *anchor*: an invoice is issued
at once, dated that day, then one every month on the same day of the month (assigned on the 20th:
the 20th of every following month). A day that does not exist in a month (the 31st in April, the 29th
to 31st in February) falls on the month's last day, and the original anchor day is kept for the later
months. **The amount is always the plan's price**, with no prorata and no per-day calculation. A new
plan assignment is a new anchor: an invoice is issued at once at the new plan's price, dated the
assignment day, and the cycles of the previous plan stop. An invoice is identified by its cycle date
(`period` = `YYYY-MM-DD`, unique per client): issuing the same cycle again never duplicates it (store
refusal + database unique key on client and period). Invoices of the former calendar-month rule keep
their `YYYY-MM` period and stay readable.

**Statuses.** A new invoice is `PENDING`. It becomes `PAID` either by the Admin's declaration
(origin `manual`, with who and when) or by a verified payment confirmation (origin `electronic`, with
the provider's transaction id and event id; see `sales_app.payment_confirmation`). `FOR_FREE` is
always a manual decision. `DISCOUNTED_100` (spec 13.3, 13.4) is a distinct, manual, owner-only decision:
the invoice is kept at its billed amount (`amount`, before discount) and records the amount after the
discount (0), the amount discounted and the amount given free, with who and when (`status_changed_by`/
`_at` and the traced `payment_events` row). Neither `FOR_FREE` nor a discount is ever a payment
received: only `PAID` counts as collected (`Invoice.is_collected`). A failed electronic payment makes it `FAILED`, which can still be paid
later (electronically or manually). `PAID`, `FOR_FREE` and `DISCOUNTED_100` are final; an invoice paid electronically
can no longer be marked manually. *Overdue* is not a stored status: it is computed (`is_overdue`) for
an invoice still `PENDING` or `FAILED` `DUE_DAYS` after the invoice date (after the end of the month for
a legacy `YYYY-MM` invoice), so it never goes stale.
"""

import calendar
import re
import threading
from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from sales_app.payment_provider import PaymentEvent, PaymentStatus
from sales_app.plans import PlanAssignment

_REFERENCE = re.compile(r"^INV-(\d{4}-(?:0[1-9]|1[0-2])(?:-\d{2})?)-(.+)$")
_PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])(?:-(\d{2}))?$")


class InvoiceStatus(StrEnum):
    PENDING = "PENDING"
    PAID = "PAID"
    FOR_FREE = "FOR_FREE"
    DISCOUNTED_100 = "DISCOUNTED_100"  # 100% discount: billed amount kept, nothing to pay, not collected
    FAILED = "FAILED"  # an electronic payment was reported failed; can still be paid


class PaymentOrigin(StrEnum):
    MANUAL = "manual"  # the Admin declared it
    ELECTRONIC = "electronic"  # a verified payment confirmation from the provider


DUE_DAYS = 15  # an unpaid invoice is overdue this many days after its date


class InvalidPeriodError(ValueError):
    """Not a `YYYY-MM-DD` cycle date (or a legacy `YYYY-MM` month)."""


class InvoiceNotFoundError(LookupError):
    """No such invoice."""


class InvoiceStatusError(ValueError):
    """The status change is not allowed (the invoice was already settled)."""


def parse_period(text: str) -> tuple[datetime, datetime]:
    """[start, end) in UTC of a `YYYY-MM-DD` cycle date (that day) or of a legacy `YYYY-MM` month."""
    match = _PERIOD.match(text)
    if match is None:
        raise InvalidPeriodError("the period must look like 2026-05-20 (or 2026-05)")
    year, month = int(match.group(1)), int(match.group(2))
    if match.group(3) is not None:
        try:
            start = datetime(year, month, int(match.group(3)), tzinfo=UTC)
        except ValueError:
            raise InvalidPeriodError("not a valid date") from None
        return start, start + timedelta(days=1)
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
    return start, end


def period_upper_bound(text: str) -> str:
    """The largest `period` string that still belongs to `text` (a month includes all its days)."""
    return text + "-99" if len(text) == 7 else text


def add_months(anchor: date, months: int) -> date:
    """`anchor` moved by `months`, on the same day of the month, or the month's last day if it has
    no such day. Always computed from the original anchor, so 31 Jan -> 28 Feb -> 31 Mar."""
    index = anchor.year * 12 + anchor.month - 1 + months
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def cycle_dates(started_at: datetime, ended_at: datetime | None, now: datetime) -> list[date]:
    """The cycle dates of an assignment that are due at `now`, oldest first: the assignment day, then
    the same day of every following month. None at or after the assignment's end (the cycles of a
    replaced plan stop), none in the future."""
    anchor = started_at.astimezone(UTC).date()
    today = now.astimezone(UTC).date()
    last = ended_at.astimezone(UTC).date() if ended_at is not None else None
    out: list[date] = []
    n = 0
    while True:
        day = add_months(anchor, n)
        if day > today or (last is not None and day >= last):
            break
        out.append(day)
        n += 1
    return out


def next_cycle_date(started_at: datetime, now: datetime) -> date:
    """The first cycle date after today for an assignment still running."""
    anchor = started_at.astimezone(UTC).date()
    today = now.astimezone(UTC).date()
    n = 0
    while add_months(anchor, n) <= today:
        n += 1
    return add_months(anchor, n)


def due_at_of(period: str) -> datetime:
    """When an invoice of `period` falls due: `DUE_DAYS` after its date (after the end of the month
    for a legacy `YYYY-MM` invoice)."""
    return parse_period(period)[1] + timedelta(days=DUE_DAYS - (1 if len(period) == 10 else 0))


@dataclass(frozen=True, slots=True)
class InvoiceLine:
    plan_id: str
    plan_name: str
    monthly_price: Decimal
    currency: str
    included_credits: int
    daily_quota: int
    amount: Decimal
    days: int = 1  # legacy prorated lines only; a line is now the plan at its price
    days_in_period: int = 1


@dataclass(frozen=True, slots=True)
class Invoice:
    invoice_id: int  # 0 until the store has kept it
    client_id: str
    period: str
    currency: str
    amount: Decimal
    status: InvoiceStatus
    lines: tuple[InvoiceLine, ...]
    created_at: datetime
    created_by: str
    status_changed_at: datetime | None = None
    status_changed_by: str | None = None
    payment_origin: PaymentOrigin | None = None
    payment_transaction_id: str | None = None
    payment_event_id: str | None = None
    # A discount (only `DISCOUNTED_100` today). `amount` stays the billed amount before discount.
    discount_percent: int = 0
    amount_after_discount: Decimal | None = None
    discount_amount: Decimal | None = None
    free_amount: Decimal | None = None
    discount_reason: str | None = None

    @property
    def is_collected(self) -> bool:
        """Money received. `FOR_FREE` and a discount are decisions, never a collection."""
        return self.status is InvoiceStatus.PAID

    @property
    def free_total(self) -> Decimal | None:
        """What was given free: the recorded amount, or the whole invoice for a legacy `FOR_FREE`."""
        if self.free_amount is not None:
            return self.free_amount
        return self.amount if self.status is InvoiceStatus.FOR_FREE else None

    @property
    def number(self) -> str:
        return f"INV-{self.period}-{self.client_id}"

    @property
    def due_at(self) -> datetime:
        return due_at_of(self.period)

    @property
    def issued_on(self) -> date:
        """The invoice date: the cycle date, or the first day of a legacy month."""
        return parse_period(self.period)[0].date()

    def is_overdue(self, now: datetime) -> bool:
        return self.status in (InvoiceStatus.PENDING, InvoiceStatus.FAILED) and now >= self.due_at


def parse_reference(reference: str) -> tuple[str, str] | None:
    """(client_id, period) of an invoice number `INV-<period>-<client_id>`; None if it is not one."""
    match = _REFERENCE.match(reference)
    return (match.group(2), match.group(1)) if match else None


class PaymentOutcome(StrEnum):
    APPLIED = "applied"  # the invoice is now PAID (electronic)
    FAILURE_RECORDED = "failure_recorded"  # the invoice is FAILED
    DUPLICATE = "duplicate"  # the event or the transaction was already processed: nothing changed
    UNKNOWN_INVOICE = "unknown_invoice"
    AMOUNT_MISMATCH = "amount_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    ALREADY_SETTLED = "already_settled"  # paid (by hand or not) or free already: left as it was
    TRANSACTION_REUSED = "transaction_reused"  # that transaction paid another invoice


@dataclass(frozen=True, slots=True)
class PaymentResult:
    outcome: PaymentOutcome
    invoice: Invoice | None


@dataclass(frozen=True, slots=True)
class PaymentRecord:
    """One traced decision about an invoice's payment: a webhook event (any outcome) or a manual
    settlement (`source` "manual", `event_id` "manual:<invoice id>")."""

    event_id: str
    source: str  # "webhook" | "manual"
    invoice_id: int | None
    client_id: str | None
    invoice_reference: str | None
    amount: Decimal | None
    currency: str | None
    transaction_id: str | None
    event_status: str  # succeeded | failed | PAID | FOR_FREE | DISCOUNTED_100
    outcome: str
    actor: str
    received_at: datetime


def decide(
    invoice: Invoice | None, event: PaymentEvent, transaction_owner: int | None
) -> PaymentOutcome:
    """What a verified event does to the invoice it names. `transaction_owner` is the id of the
    invoice that this transaction already paid, if any."""
    if invoice is None:
        return PaymentOutcome.UNKNOWN_INVOICE
    if event.status is PaymentStatus.FAILED:
        if invoice.status in (InvoiceStatus.PENDING, InvoiceStatus.FAILED):
            return PaymentOutcome.FAILURE_RECORDED
        return PaymentOutcome.ALREADY_SETTLED
    if transaction_owner is not None:
        if transaction_owner == invoice.invoice_id:
            return PaymentOutcome.DUPLICATE
        return PaymentOutcome.TRANSACTION_REUSED
    if invoice.status not in (InvoiceStatus.PENDING, InvoiceStatus.FAILED):
        return PaymentOutcome.ALREADY_SETTLED
    if event.currency != invoice.currency:
        return PaymentOutcome.CURRENCY_MISMATCH
    if event.amount != invoice.amount:
        return PaymentOutcome.AMOUNT_MISMATCH
    return PaymentOutcome.APPLIED


def build_invoice(
    assignment: PlanAssignment, cycle: date, *, now: datetime, created_by: str
) -> Invoice:
    """The invoice of one cycle: one line, the plan at its price (no prorata, ever)."""
    return Invoice(
        invoice_id=0,
        client_id=assignment.client_id,
        period=cycle.isoformat(),
        currency=assignment.currency,
        amount=assignment.monthly_price,
        status=InvoiceStatus.PENDING,
        lines=(
            InvoiceLine(
                plan_id=assignment.plan_id,
                plan_name=assignment.plan_name,
                monthly_price=assignment.monthly_price,
                currency=assignment.currency,
                included_credits=assignment.included_credits,
                daily_quota=assignment.daily_quota,
                amount=assignment.monthly_price,
            ),
        ),
        created_at=now,
        created_by=created_by,
    )


@dataclass(frozen=True, slots=True)
class InvoiceFilter:
    """Every field narrows. `client_ids` is the set the asker may see (None: all clients)."""

    client_ids: Collection[str] | None = None
    client_id: str | None = None
    period_from: str | None = None
    period_to: str | None = None
    status: InvoiceStatus | None = None

    def matches(self, invoice: Invoice) -> bool:
        return (
            (self.client_ids is None or invoice.client_id in self.client_ids)
            and (self.client_id is None or invoice.client_id == self.client_id)
            and (self.period_from is None or invoice.period >= self.period_from)
            and (self.period_to is None or invoice.period <= period_upper_bound(self.period_to))
            and (self.status is None or invoice.status is self.status)
        )


class InvoiceStore(Protocol):
    def create(self, invoice: Invoice) -> Invoice | None:
        """Keeps the invoice with its lines, all or nothing, and returns it with its id. None if
        the client already has an invoice for that period (which is left exactly as it was)."""
        ...

    def list_invoices(self, where: InvoiceFilter) -> list[Invoice]:
        """Newest period first, then by client."""
        ...

    def get(self, invoice_id: int) -> Invoice | None: ...

    def settle(
        self,
        invoice_id: int,
        status: InvoiceStatus,
        *,
        at: datetime,
        by: str,
        reason: str | None = None,
    ) -> Invoice:
        """Manual settlement: PENDING (or FAILED) becomes PAID, FOR_FREE or DISCOUNTED_100, once,
        origin `manual`, and the decision is traced. `InvoiceNotFoundError`; `InvoiceStatusError` if
        it is settled already (a payment confirmed electronically included) or `status` is not one
        of those. `reason` is kept with a discount only."""
        ...

    def process_payment(self, event: PaymentEvent, *, at: datetime, by: str) -> PaymentResult:
        """Applies one verified payment event atomically and idempotently (see
        `sales_app.payment_confirmation`). Every distinct event is traced, rejected ones included."""
        ...

    def payment_events(self, invoice_id: int | None = None) -> list[PaymentRecord]:
        """The traced events (newest first), of one invoice or all."""
        ...


MANUAL_STATUSES = (InvoiceStatus.PAID, InvoiceStatus.FOR_FREE, InvoiceStatus.DISCOUNTED_100)


def discount_100(invoice: Invoice, reason: str | None) -> dict[str, object]:
    """The fields a 100% discount sets: billed amount untouched, nothing left to pay, the whole billed
    amount discounted and given free."""
    return {
        "discount_percent": 100,
        "amount_after_discount": Decimal("0.00"),
        "discount_amount": invoice.amount,
        "free_amount": invoice.amount,
        "discount_reason": (reason or "").strip()[:255] or None,
    }


def check_settle(invoice: Invoice, status: InvoiceStatus) -> None:
    if status not in MANUAL_STATUSES:
        raise InvoiceStatusError("an invoice can only be marked PAID, FOR_FREE or DISCOUNTED_100")
    if invoice.status is InvoiceStatus.PAID and invoice.payment_origin is PaymentOrigin.ELECTRONIC:
        raise InvoiceStatusError("this invoice was paid electronically and cannot be changed")
    if invoice.status not in (InvoiceStatus.PENDING, InvoiceStatus.FAILED):
        raise InvoiceStatusError("only an unpaid invoice can be settled")


class InMemoryInvoiceStore:
    """For tests: nothing survives the process."""

    def __init__(self) -> None:
        self._invoices: dict[int, Invoice] = {}
        self._records: list[PaymentRecord] = []
        self._lock = threading.Lock()

    def create(self, invoice: Invoice) -> Invoice | None:
        with self._lock:
            if any(
                i.client_id == invoice.client_id and i.period == invoice.period
                for i in self._invoices.values()
            ):
                return None
            kept = replace(invoice, invoice_id=len(self._invoices) + 1)
            self._invoices[kept.invoice_id] = kept
            return kept

    def list_invoices(self, where: InvoiceFilter) -> list[Invoice]:
        with self._lock:
            found = [i for i in self._invoices.values() if where.matches(i)]
        return sorted(found, key=lambda i: (_neg(i.period), i.client_id))

    def get(self, invoice_id: int) -> Invoice | None:
        with self._lock:
            return self._invoices.get(invoice_id)

    def settle(
        self,
        invoice_id: int,
        status: InvoiceStatus,
        *,
        at: datetime,
        by: str,
        reason: str | None = None,
    ) -> Invoice:
        with self._lock:
            invoice = self._invoices.get(invoice_id)
            if invoice is None:
                raise InvoiceNotFoundError(invoice_id)
            check_settle(invoice, status)
            settled = replace(
                invoice,
                status=status,
                status_changed_at=at,
                status_changed_by=by,
                payment_origin=PaymentOrigin.MANUAL,
                **(discount_100(invoice, reason) if status is InvoiceStatus.DISCOUNTED_100 else {}),
            )
            self._invoices[invoice_id] = settled
            self._records.append(manual_record(settled, status, at=at, by=by))
            return settled

    def process_payment(self, event: PaymentEvent, *, at: datetime, by: str) -> PaymentResult:
        with self._lock:
            known = next((r for r in self._records if r.event_id == event.event_id), None)
            if known is not None:
                return PaymentResult(
                    PaymentOutcome.DUPLICATE, self._invoices.get(known.invoice_id or 0)
                )
            invoice = None
            parsed = parse_reference(event.invoice_reference)
            if parsed is not None:
                invoice = next(
                    (i for i in self._invoices.values() if (i.client_id, i.period) == parsed),
                    None,
                )
            owner = next(
                (
                    i.invoice_id
                    for i in self._invoices.values()
                    if i.payment_transaction_id == event.transaction_id
                ),
                None,
            )
            outcome = decide(invoice, event, owner)
            if outcome is PaymentOutcome.APPLIED:
                assert invoice is not None
                invoice = replace(
                    invoice,
                    status=InvoiceStatus.PAID,
                    status_changed_at=at,
                    status_changed_by=by,
                    payment_origin=PaymentOrigin.ELECTRONIC,
                    payment_transaction_id=event.transaction_id,
                    payment_event_id=event.event_id,
                )
                self._invoices[invoice.invoice_id] = invoice
            elif outcome is PaymentOutcome.FAILURE_RECORDED:
                assert invoice is not None
                if invoice.status is InvoiceStatus.PENDING:
                    invoice = replace(
                        invoice,
                        status=InvoiceStatus.FAILED,
                        status_changed_at=at,
                        status_changed_by=by,
                    )
                    self._invoices[invoice.invoice_id] = invoice
            self._records.append(webhook_record(event, invoice, outcome, at=at, by=by))
            return PaymentResult(outcome, invoice)

    def payment_events(self, invoice_id: int | None = None) -> list[PaymentRecord]:
        with self._lock:
            found = [r for r in self._records if invoice_id in (None, r.invoice_id)]
        return list(reversed(found))


def manual_record(
    invoice: Invoice, status: InvoiceStatus, *, at: datetime, by: str
) -> PaymentRecord:
    return PaymentRecord(
        event_id=f"manual:{invoice.invoice_id}",
        source="manual",
        invoice_id=invoice.invoice_id,
        client_id=invoice.client_id,
        invoice_reference=invoice.number,
        amount=invoice.amount,
        currency=invoice.currency,
        transaction_id=None,
        event_status=status.value,
        outcome="applied",
        actor=by,
        received_at=at,
    )


def webhook_record(
    event: PaymentEvent,
    invoice: Invoice | None,
    outcome: PaymentOutcome,
    *,
    at: datetime,
    by: str,
) -> PaymentRecord:
    return PaymentRecord(
        event_id=event.event_id,
        source="webhook",
        invoice_id=invoice.invoice_id if invoice else None,
        client_id=invoice.client_id if invoice else None,
        invoice_reference=event.invoice_reference,
        amount=event.amount,
        currency=event.currency,
        transaction_id=event.transaction_id,
        event_status=event.status.value,
        outcome=outcome.value,
        actor=by,
        received_at=at,
    )


def _neg(period: str) -> tuple[int, ...]:
    return tuple(-ord(c) for c in period)
