"""The finance contracts: what the Admin dashboards read (documented in docs/finance-contracts.md).

Money is `Decimal` here and a decimal string on the wire. **Amounts of different currencies are never
added**: every figure is given per currency. Only an invoice that is `PAID` is money collected; a
`FOR_FREE` invoice or a discount is a decision, never an encashment.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class InvoiceAggregate:
    """Invoices of one (period, currency, status), summed by the store (SQL or memory).

    `after_discount` is the sum of what is left to pay after a discount (`amount` when none).
    `free` is what was given free (`FOR_FREE`: the whole amount; a discount: the free amount)."""

    period: str
    currency: str
    status: str
    count: int
    billed: Decimal
    after_discount: Decimal
    discounted: Decimal
    free: Decimal


@dataclass(frozen=True, slots=True)
class SubscriptionPoint:
    """Clients on a plan (`active`) and clients that have been on one but are not (`inactive`) at the
    end of `period`."""

    period: str
    active: int
    inactive: int


@dataclass(slots=True)
class Totals:
    currency: str
    billed: Decimal = Decimal(0)  # before any discount
    after_discount: Decimal = Decimal(0)
    collected: Decimal = Decimal(0)  # PAID only
    free: Decimal = Decimal(0)
    discounted: Decimal = Decimal(0)
    pending: Decimal = Decimal(0)  # unpaid (PENDING, FAILED), not yet overdue
    overdue: Decimal = Decimal(0)  # unpaid and past `due_at`
    issued: int = 0
    paid: int = 0
    free_count: int = 0  # FOR_FREE
    discounted_count: int = 0  # DISCOUNTED_100
    pending_count: int = 0
    overdue_count: int = 0


@dataclass(slots=True)
class TimelinePoint:
    period: str
    currency: str
    billed: Decimal = Decimal(0)
    collected: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class Profitability:
    """Revenue minus costs, per currency. `status` "not_connected": no cost source is wired yet
    (the provider costs of lot 2C); nothing is estimated."""

    status: str
    costs: dict[str, Decimal] = field(default_factory=dict)
    margin: dict[str, Decimal] = field(default_factory=dict)  # collected - costs


@dataclass(frozen=True, slots=True)
class FinanceSummary:
    period_from: str
    period_to: str
    client_id: str | None
    generated_at: datetime
    totals: list[Totals]  # one per currency
    timeline: list[TimelinePoint]  # per month and currency, oldest first
    subscriptions: list[SubscriptionPoint]  # per month, oldest first
    profitability: Profitability
