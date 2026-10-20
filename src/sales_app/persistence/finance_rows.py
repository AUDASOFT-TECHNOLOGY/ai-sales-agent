"""The SQL half of the financial aggregations (`sales_app.finance`).

Sums and counts are computed by the database, grouped by period, currency and status: the pure side
decides what they mean (collected, overdue...). Read-only. A database that cannot be reached is
`StoreUnavailableError`, a fixed sentence.
"""

from collections.abc import Collection
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import Engine, and_, case, distinct, func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from sales_app.billing import InvoiceStatus, parse_period
from sales_app.finance.contracts import InvoiceAggregate, SubscriptionPoint
from sales_app.persistence.tables import client_plan_assignments as assignments
from sales_app.persistence.tables import invoices
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the finance figures are unavailable"
_ZERO = Decimal("0.00")


def _money(value) -> Decimal:
    return Decimal(value) if value is not None else _ZERO


class SqlFinanceReader:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlFinanceReader()"

    def invoice_aggregates(
        self,
        *,
        period_from: str,
        period_to: str,
        client_id: str | None,
        client_ids: Collection[str] | None,
    ) -> list[InvoiceAggregate]:
        free = case(
            (invoices.c.free_amount.is_not(None), invoices.c.free_amount),
            (invoices.c.status == InvoiceStatus.FOR_FREE.value, invoices.c.amount),
            else_=0,
        )
        query = (
            select(
                invoices.c.period,
                invoices.c.currency,
                invoices.c.status,
                func.count(),
                func.sum(invoices.c.amount),
                func.sum(func.coalesce(invoices.c.amount_after_discount, invoices.c.amount)),
                func.sum(func.coalesce(invoices.c.discount_amount, 0)),
                func.sum(free),
            )
            .where(invoices.c.period >= period_from, invoices.c.period <= period_to)
            .group_by(invoices.c.period, invoices.c.currency, invoices.c.status)
            .order_by(invoices.c.period, invoices.c.currency, invoices.c.status)
        )
        if client_id is not None:
            query = query.where(invoices.c.client_id == client_id)
        if client_ids is not None:
            query = query.where(invoices.c.client_id.in_(list(client_ids)))
        try:
            with self._engine.connect() as connection:
                return [
                    InvoiceAggregate(
                        period=period,
                        currency=currency,
                        status=status,
                        count=count,
                        billed=_money(billed),
                        after_discount=_money(after),
                        discounted=_money(discounted),
                        free=_money(given_free),
                    )
                    for period, currency, status, count, billed, after, discounted, given_free in (
                        connection.execute(query)
                    )
                ]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def subscription_points(
        self,
        periods: list[str],
        *,
        client_id: str | None,
        client_ids: Collection[str] | None,
    ) -> list[SubscriptionPoint]:
        points: list[SubscriptionPoint] = []
        try:
            with self._engine.connect() as connection:
                for period in periods:
                    at = parse_period(period)[1] - timedelta(microseconds=1)
                    started = assignments.c.started_at <= at
                    in_force = and_(
                        started, or_(assignments.c.ended_at.is_(None), assignments.c.ended_at > at)
                    )
                    query = select(
                        func.count(distinct(case((in_force, assignments.c.client_id)))),
                        func.count(distinct(case((started, assignments.c.client_id)))),
                    )
                    if client_id is not None:
                        query = query.where(assignments.c.client_id == client_id)
                    if client_ids is not None:
                        query = query.where(assignments.c.client_id.in_(list(client_ids)))
                    active, seen = connection.execute(query).one()
                    points.append(SubscriptionPoint(period, active, seen - active))
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return points
