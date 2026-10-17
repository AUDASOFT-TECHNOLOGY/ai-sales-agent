"""Monthly invoices: what each client owes for a calendar month, from the plan it was on.

**Where the amounts come from.** An invoice is built from the client's plan assignments
(`sales_app.plans`), which keep the plan's terms as they were when the client was put on it. The
month is a calendar month in UTC. Every day of the month belongs to the assignment in force at the
*end* of that day; a client on a plan for the whole month owes exactly its monthly price, and one
that changed plan (or joined, or left) mid-month owes each plan for its days only:
`price × days / days in the month`, rounded to the cent per line. A day with no plan costs nothing,
and a client with no plan day in the month gets no invoice. Mixed currencies in one month cannot be
added up: such a client is reported, not invoiced.

**A history that does not move.** An invoice carries its own copy of every line (plan name, price,
currency, credits, quota, days, amount). Editing or deactivating a plan afterwards changes nothing
already invoiced, and generating a period again never touches an invoice that exists (one invoice
per client and period, enforced by the database).

**Statuses.** A new invoice is `PENDING`. The Admin declares it `PAID` or `FOR_FREE`, once, with who
and when. Revansa does not collect or verify any payment: `PAID` is the Admin's statement, and no
payment gateway is involved. Only a month that has ended can be invoiced.
"""

import re
import threading
from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Protocol

from sales_app.plans import PlanAssignment

_PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_CENT = Decimal("0.01")


class InvoiceStatus(StrEnum):
    PENDING = "PENDING"
    PAID = "PAID"
    FOR_FREE = "FOR_FREE"


class InvalidPeriodError(ValueError):
    """Not a `YYYY-MM` month, or a month that cannot be invoiced (yet)."""


class MixedCurrenciesError(ValueError):
    """The client was on plans in different currencies during the month."""


class InvoiceNotFoundError(LookupError):
    """No such invoice."""


class InvoiceStatusError(ValueError):
    """The status change is not allowed (the invoice was already settled)."""


def parse_period(text: str) -> tuple[datetime, datetime]:
    """[start, end) of a `YYYY-MM` month, in UTC."""
    match = _PERIOD.match(text)
    if match is None:
        raise InvalidPeriodError("the period must look like 2026-05")
    year, month = int(match.group(1)), int(match.group(2))
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
    return start, end


def ensure_closed(period: str, now: datetime) -> tuple[datetime, datetime]:
    """The bounds of `period`, which must have ended: an unfinished month is not invoiced."""
    start, end = parse_period(period)
    if end > now:
        raise InvalidPeriodError("only a month that has ended can be invoiced")
    return start, end


@dataclass(frozen=True, slots=True)
class InvoiceLine:
    plan_id: str
    plan_name: str
    monthly_price: Decimal
    currency: str
    included_credits: int
    daily_quota: int
    days: int
    days_in_period: int
    amount: Decimal


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

    @property
    def number(self) -> str:
        return f"INV-{self.period}-{self.client_id}"


def _day_owner(day_end: datetime, assignments: Iterable[PlanAssignment]) -> PlanAssignment | None:
    for a in assignments:
        if a.started_at <= day_end and (a.ended_at is None or a.ended_at > day_end):
            return a
    return None


def build_invoice(
    client_id: str,
    period: str,
    assignments: Iterable[PlanAssignment],
    *,
    now: datetime,
    created_by: str,
) -> Invoice | None:
    """The invoice of one client for a closed month; None if it had no plan day in it.
    `MixedCurrenciesError` if its plans were in different currencies."""
    start, end = ensure_closed(period, now)
    mine = [a for a in assignments if a.client_id == client_id]
    days_in_period = (end - start).days
    days: dict[tuple[str, datetime], int] = {}  # one entry per assignment (plan, start)
    owners: dict[tuple[str, datetime], PlanAssignment] = {}
    for offset in range(days_in_period):
        day_end = start + timedelta(days=offset + 1) - timedelta(microseconds=1)
        owner = _day_owner(day_end, mine)
        if owner is not None:
            key = (owner.plan_id, owner.started_at)
            days[key] = days.get(key, 0) + 1
            owners[key] = owner
    if not days:
        return None
    lines = []
    for key in sorted(days, key=lambda k: k[1]):
        a, n = owners[key], days[key]
        lines.append(
            InvoiceLine(
                plan_id=a.plan_id,
                plan_name=a.plan_name,
                monthly_price=a.monthly_price,
                currency=a.currency,
                included_credits=a.included_credits,
                daily_quota=a.daily_quota,
                days=n,
                days_in_period=days_in_period,
                amount=(a.monthly_price * n / days_in_period).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                ),
            )
        )
    currencies = {line.currency for line in lines}
    if len(currencies) > 1:
        raise MixedCurrenciesError(client_id)
    return Invoice(
        invoice_id=0,
        client_id=client_id,
        period=period,
        currency=lines[0].currency,
        amount=sum((line.amount for line in lines), Decimal(0)),
        status=InvoiceStatus.PENDING,
        lines=tuple(lines),
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
            and (self.period_to is None or invoice.period <= self.period_to)
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

    def settle(self, invoice_id: int, status: InvoiceStatus, *, at: datetime, by: str) -> Invoice:
        """PENDING becomes PAID or FOR_FREE, once. `InvoiceNotFoundError`; `InvoiceStatusError` if
        it is not pending any more (or `status` is PENDING)."""
        ...


def check_settle(invoice: Invoice, status: InvoiceStatus) -> None:
    if status is InvoiceStatus.PENDING or invoice.status is not InvoiceStatus.PENDING:
        raise InvoiceStatusError("only a pending invoice can be marked PAID or FOR_FREE")


class InMemoryInvoiceStore:
    """For tests: nothing survives the process."""

    def __init__(self) -> None:
        self._invoices: dict[int, Invoice] = {}
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

    def settle(self, invoice_id: int, status: InvoiceStatus, *, at: datetime, by: str) -> Invoice:
        with self._lock:
            invoice = self._invoices.get(invoice_id)
            if invoice is None:
                raise InvoiceNotFoundError(invoice_id)
            check_settle(invoice, status)
            settled = replace(invoice, status=status, status_changed_at=at, status_changed_by=by)
            self._invoices[invoice_id] = settled
            return settled


def _neg(period: str) -> tuple[int, ...]:
    return tuple(-ord(c) for c in period)
