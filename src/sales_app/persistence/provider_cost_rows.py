"""The SQL side of the technical costs (the port `CostStore` of `sales_app.provider_costs`).

Global tables (the Admin's tariffs, the cost lines) plus read-only aggregates over two registers that
already exist: the LLM usage register and the WhatsApp delivery register. Every aggregate is a SQL
`GROUP BY`; a client filter is applied in the statement. A database that cannot be reached is
`StoreUnavailableError`, a fixed sentence.
"""

from dataclasses import replace
from datetime import datetime

from sqlalchemy import Engine, and_, case, delete, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from sales_app.persistence.tables import cost_rates as rates
from sales_app.persistence.tables import op_llm_usage as usage
from sales_app.persistence.tables import provider_costs as costs
from sales_app.persistence.tables import wa_deliveries as deliveries
from sales_app.provider_costs import (
    COMPUTED_PREFIX,
    CostLine,
    CostRate,
    LLMUsageTotal,
    Period,
    parse_period,
)
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the cost register is unavailable"
_FAILED = "failed"


def _line(row) -> CostLine:
    return CostLine(
        id=row.id,
        client_id=row.client_id,
        provider=row.provider,
        category=row.category,
        period=parse_period(
            row.period_date.isoformat()
            if row.period_kind == "day"
            else row.period_date.isoformat()[:7]
        ),
        quantity=row.quantity,
        unit=row.unit,
        unit_price=row.unit_price,
        amount=row.amount,
        currency=row.currency,
        basis=row.basis,
        source=row.source,
    )


def _values(line: CostLine, *, by: str, at: datetime) -> dict:
    return {
        "client_id": line.client_id,
        "provider": line.provider,
        "category": line.category,
        "period_date": line.period.start,
        "period_kind": line.period.kind,
        "quantity": line.quantity,
        "unit": line.unit,
        "unit_price": line.unit_price,
        "amount": line.amount,
        "currency": line.currency,
        "basis": line.basis,
        "source": line.source,
        "recorded_at": at,
        "recorded_by": by,
    }


class SqlCostStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlCostStore()"

    # ---- tariffs ------------------------------------------------------------------------------

    def list_rates(self) -> list[CostRate]:
        try:
            with self._engine.connect() as connection:
                return [
                    CostRate(
                        provider=r.provider,
                        key=r.rate_key,
                        unit=r.unit,
                        unit_price=r.unit_price,
                        currency=r.currency,
                        updated_at=r.updated_at,
                        updated_by=r.updated_by,
                    )
                    for r in connection.execute(
                        select(rates).order_by(rates.c.provider, rates.c.rate_key)
                    )
                ]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def set_rate(self, rate: CostRate) -> None:
        values = {
            "unit": rate.unit,
            "unit_price": rate.unit_price,
            "currency": rate.currency,
            "updated_at": rate.updated_at,
            "updated_by": rate.updated_by,
        }
        try:
            with self._engine.begin() as connection:
                changed = connection.execute(
                    update(rates)
                    .where(rates.c.provider == rate.provider, rates.c.rate_key == rate.key)
                    .values(**values)
                )
                if changed.rowcount == 0:
                    connection.execute(
                        insert(rates).values(provider=rate.provider, rate_key=rate.key, **values)
                    )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def delete_rate(self, provider: str, key: str) -> bool:
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    delete(rates).where(rates.c.provider == provider, rates.c.rate_key == key)
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return result.rowcount > 0

    # ---- measured sources ---------------------------------------------------------------------

    def llm_totals(self, period: Period, client_id: str | None) -> list[LLMUsageTotal]:
        both = and_(usage.c.input_tokens.is_not(None), usage.c.output_tokens.is_not(None))
        query = (
            select(
                usage.c.client_id,
                usage.c.provider,
                usage.c.model,
                func.count().label("calls"),
                func.sum(case((both, 1), else_=0)).label("costed"),
                func.sum(case((both, usage.c.input_tokens), else_=0)).label("tokens_in"),
                func.sum(case((both, usage.c.output_tokens), else_=0)).label("tokens_out"),
            )
            .where(
                usage.c.occurred_at >= period.start_at,
                usage.c.occurred_at < period.end_at,
                usage.c.status == "ok",  # a failed call consumed nothing we can measure
            )
            .group_by(usage.c.client_id, usage.c.provider, usage.c.model)
            .order_by(usage.c.client_id, usage.c.provider, usage.c.model)
        )
        if client_id is not None:
            query = query.where(usage.c.client_id == client_id)
        try:
            with self._engine.connect() as connection:
                return [
                    LLMUsageTotal(
                        client_id=r.client_id,
                        provider=r.provider,
                        model=r.model,
                        calls=int(r.calls),
                        costed_calls=int(r.costed or 0),
                        input_tokens=int(r.tokens_in or 0),
                        output_tokens=int(r.tokens_out or 0),
                    )
                    for r in connection.execute(query)
                ]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def whatsapp_sent(self, period: Period, client_id: str | None) -> dict[str, int]:
        query = (
            select(deliveries.c.client_id, func.count())
            .where(
                deliveries.c.at >= period.start_at,
                deliveries.c.at < period.end_at,
                deliveries.c.status != _FAILED,
            )
            .group_by(deliveries.c.client_id)
        )
        if client_id is not None:
            query = query.where(deliveries.c.client_id == client_id)
        try:
            with self._engine.connect() as connection:
                return {row[0]: int(row[1]) for row in connection.execute(query)}
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    # ---- cost lines ---------------------------------------------------------------------------

    def entries(self, period: Period, client_id: str | None) -> list[CostLine]:
        query = (
            select(costs)
            .where(
                costs.c.period_date >= period.start,
                costs.c.period_date < period.end,
                ~costs.c.source.like(COMPUTED_PREFIX + "%"),
            )
            .order_by(costs.c.period_date, costs.c.id)
        )
        if client_id is not None:
            query = query.where(costs.c.client_id == client_id)
        try:
            with self._engine.connect() as connection:
                return [_line(r) for r in connection.execute(query)]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def add_entry(self, line: CostLine, *, by: str, at: datetime) -> CostLine:
        try:
            with self._engine.begin() as connection:
                result = connection.execute(insert(costs).values(**_values(line, by=by, at=at)))
                new_id = int(result.inserted_primary_key[0])
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return replace(line, id=new_id)

    def delete_entry(self, entry_id: int) -> bool:
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    delete(costs).where(
                        costs.c.id == entry_id, ~costs.c.source.like(COMPUTED_PREFIX + "%")
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return result.rowcount > 0

    def replace_computed(
        self, period: Period, client_id: str | None, lines: list[CostLine], *, by: str, at: datetime
    ) -> int:
        stale = delete(costs).where(
            costs.c.source.like(COMPUTED_PREFIX + "%"),
            costs.c.period_kind == period.kind,
            costs.c.period_date == period.start,
        )
        if client_id is not None:
            stale = stale.where(costs.c.client_id == client_id)
        try:
            with self._engine.begin() as connection:
                connection.execute(stale)
                if lines:
                    connection.execute(
                        insert(costs), [_values(line, by=by, at=at) for line in lines]
                    )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return len(lines)
