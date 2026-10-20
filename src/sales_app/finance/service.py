"""Financial aggregations over invoices and plan assignments.

`FinanceService.summary(...)` folds the per-(period, currency, status) sums that a `FinanceReader`
returns (SQL in production: `sales_app.persistence.finance_rows.SqlFinanceReader`; or computed from
the in-memory stores) into the contract of `sales_app.finance.contracts`. The rules live here, once:

- billed = the invoices' `amount` (before discount); after discount = what is left to pay;
- collected = `PAID` invoices only. `FOR_FREE` and `DISCOUNTED_100` add to `free` / `discounted`
  and are never collected;
- pending = `PENDING` / `FAILED` before `due_at`, overdue = the same from `due_at` on
  (`Invoice.due_at`: `DUE_DAYS` after the end of the month), computed from the clock;
- the timeline is by invoice period (what was billed that month, and how much of it is collected);
- profitability is an extension point (`CostSource`): without one it says `not_connected`. Nothing
  is estimated.
"""

from collections.abc import Collection
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sales_app.billing import DUE_DAYS, InvalidPeriodError, InvoiceFilter, InvoiceStatus, parse_period
from sales_app.finance.contracts import (
    FinanceSummary,
    InvoiceAggregate,
    Profitability,
    SubscriptionPoint,
    TimelinePoint,
    Totals,
)

MAX_MONTHS = 60
ZERO = Decimal("0.00")
UNPAID = (InvoiceStatus.PENDING.value, InvoiceStatus.FAILED.value)


class FinanceReader(Protocol):
    def invoice_aggregates(
        self,
        *,
        period_from: str,
        period_to: str,
        client_id: str | None,
        client_ids: Collection[str] | None,
    ) -> list[InvoiceAggregate]: ...

    def subscription_points(
        self,
        periods: list[str],
        *,
        client_id: str | None,
        client_ids: Collection[str] | None,
    ) -> list[SubscriptionPoint]: ...


class CostSource(Protocol):
    """Extension point of the profitability (lot 2C `provider_costs`): the costs per currency over
    the same period and client filter. Not wired yet."""

    def costs(
        self,
        *,
        period_from: str,
        period_to: str,
        client_id: str | None,
        client_ids: Collection[str] | None,
    ) -> dict[str, Decimal]: ...


def months_between(period_from: str, period_to: str) -> list[str]:
    start, _ = parse_period(period_from)
    end, _ = parse_period(period_to)
    if end < start:
        raise InvalidPeriodError("'from' must not be after 'to'")
    out: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(f"{year:04d}-{month:02d}")
        year, month = year + (month == 12), month % 12 + 1
    if len(out) > MAX_MONTHS:
        raise InvalidPeriodError(f"at most {MAX_MONTHS} months")
    return out


def month_end(period: str) -> datetime:
    return parse_period(period)[1]


class FinanceService:
    def __init__(self, reader: FinanceReader, costs: CostSource | None = None) -> None:
        self._reader = reader
        self._costs = costs

    def summary(
        self,
        *,
        period_from: str,
        period_to: str,
        now: datetime,
        client_id: str | None = None,
        client_ids: Collection[str] | None = None,
    ) -> FinanceSummary:
        """`client_id`: one client; `client_ids`: the clients the asker may see (None: all)."""
        months = months_between(period_from, period_to)
        scope = {"client_id": client_id, "client_ids": client_ids}
        rows = self._reader.invoice_aggregates(
            period_from=period_from, period_to=period_to, **scope
        )
        totals: dict[str, Totals] = {}
        timeline: dict[tuple[str, str], TimelinePoint] = {}
        for row in rows:
            t = totals.setdefault(row.currency, Totals(currency=row.currency))
            point = timeline.setdefault(
                (row.period, row.currency), TimelinePoint(row.period, row.currency)
            )
            t.issued += row.count
            t.billed += row.billed
            t.after_discount += row.after_discount
            t.discounted += row.discounted
            t.free += row.free
            point.billed += row.billed
            if row.status == InvoiceStatus.PAID.value:
                t.paid += row.count
                t.collected += row.billed
                point.collected += row.billed
            elif row.status == InvoiceStatus.FOR_FREE.value:
                t.free_count += row.count
            elif row.status == InvoiceStatus.DISCOUNTED_100.value:
                t.discounted_count += row.count
            elif row.status in UNPAID:
                if now >= month_end(row.period) + timedelta(days=DUE_DAYS):
                    t.overdue += row.after_discount
                    t.overdue_count += row.count
                else:
                    t.pending += row.after_discount
                    t.pending_count += row.count
        ordered_totals = [totals[c] for c in sorted(totals)]
        ordered_timeline = sorted(timeline.values(), key=lambda p: (p.period, p.currency))
        return FinanceSummary(
            period_from=period_from,
            period_to=period_to,
            client_id=client_id,
            generated_at=now,
            totals=ordered_totals,
            timeline=ordered_timeline,
            subscriptions=self._reader.subscription_points(months, **scope),
            profitability=self._profitability(ordered_totals, period_from, period_to, scope),
        )

    def _profitability(
        self, totals: list[Totals], period_from: str, period_to: str, scope: dict
    ) -> Profitability:
        if self._costs is None:
            return Profitability(status="not_connected")
        costs = self._costs.costs(period_from=period_from, period_to=period_to, **scope)
        collected = {t.currency: t.collected for t in totals}
        currencies = sorted(set(costs) | set(collected))
        return Profitability(
            status="available",
            costs={c: costs.get(c, ZERO) for c in currencies},
            margin={c: collected.get(c, ZERO) - costs.get(c, ZERO) for c in currencies},
        )


class StoreFinanceReader:
    """The same figures computed from the invoice and plan stores (tests, and any deployment whose
    stores are not SQL). Reads everything in range: not meant for big volumes."""

    def __init__(self, invoices, plans) -> None:
        self._invoices = invoices
        self._plans = plans

    def invoice_aggregates(
        self,
        *,
        period_from: str,
        period_to: str,
        client_id: str | None,
        client_ids: Collection[str] | None,
    ) -> list[InvoiceAggregate]:
        found = self._invoices.list_invoices(
            InvoiceFilter(
                client_ids=client_ids,
                client_id=client_id,
                period_from=period_from,
                period_to=period_to,
            )
        )
        groups: dict[tuple[str, str, str], list] = {}
        for i in found:
            groups.setdefault((i.period, i.currency, i.status.value), []).append(i)
        out = []
        for (period, currency, status), items in sorted(groups.items()):
            out.append(
                InvoiceAggregate(
                    period=period,
                    currency=currency,
                    status=status,
                    count=len(items),
                    billed=sum((i.amount for i in items), ZERO),
                    after_discount=sum(
                        (
                            i.amount_after_discount if i.amount_after_discount is not None else i.amount
                            for i in items
                        ),
                        ZERO,
                    ),
                    discounted=sum((i.discount_amount or ZERO for i in items), ZERO),
                    free=sum((i.free_total or ZERO for i in items), ZERO),
                )
            )
        return out

    def subscription_points(
        self,
        periods: list[str],
        *,
        client_id: str | None,
        client_ids: Collection[str] | None,
    ) -> list[SubscriptionPoint]:
        if not periods:
            return []
        assignments = self._plans.assignments_between(
            parse_period(periods[0])[0] - timedelta(days=36500), parse_period(periods[-1])[1]
        )
        mine = [
            a
            for a in assignments
            if (client_id is None or a.client_id == client_id)
            and (client_ids is None or a.client_id in client_ids)
        ]
        points = []
        for period in periods:
            at = month_end(period) - timedelta(microseconds=1)
            seen = {a.client_id for a in mine if a.started_at <= at}
            active = {
                a.client_id
                for a in mine
                if a.started_at <= at and (a.ended_at is None or a.ended_at > at)
            }
            points.append(SubscriptionPoint(period, len(active), len(seen - active)))
        return points
