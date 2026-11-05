"""The SQL side of the AI credits (`sales_app.credits`).

**The database decides.** A reservation is one conditional `UPDATE` per period
(`held = held + amount WHERE consumed + held + amount <= allowance`) in one transaction: two
processes can never reserve the same last credits, and a period that cannot hold the amount rolls
the others back. Settling is one transaction too: the ledger row (`op_llm_usage`, unique on
`(client, call id)`), the release of the reservation and the consumed counters. A second settlement
of the same call hits the unique key and changes nothing. A counter never goes above its allowance.
A database that cannot be reached is `StoreUnavailableError`, a fixed sentence.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, Engine, case, delete, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sales_app.credits import Hold
from sales_app.llm_usage import LLMUsageRecord
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import op_credit_counters as counters
from sales_app.persistence.tables import op_credit_holds as holds
from sales_app.persistence.tables import op_llm_usage as usage
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the credits are unavailable"
_SEP = "|"


class _Short(Exception):
    """A period has no room for the reservation: the whole reservation is rolled back."""


def _dialect_insert(dialect: str):
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    else:  # pragma: no cover - only the two supported backends
        raise StoreUnavailableError(_UNAVAILABLE)
    return dialect_insert


def _ensure_row(connection: Connection, client_id: str, period: str) -> None:
    statement = (
        _dialect_insert(connection.dialect.name)(counters)
        .values(client_id=client_id, period=period, consumed_nano=0, held_nano=0)
        .on_conflict_do_nothing()
    )
    connection.execute(statement)


def _drop_held(connection: Connection, client_id: str, periods: list[str], amount: int) -> None:
    for period in periods:
        connection.execute(
            update(counters)
            .where(counters.c.client_id == client_id, counters.c.period == period)
            .values(
                held_nano=case(
                    (counters.c.held_nano > amount, counters.c.held_nano - amount), else_=0
                )
            )
        )


def _free(connection: Connection, client_id: str, hold_id: str) -> None:
    """Deletes the hold and gives its credits back; whoever deletes it does that, once."""
    row = connection.execute(
        select(holds).where(holds.c.hold_id == hold_id, holds.c.client_id == client_id)
    ).first()
    if row is None:
        return
    gone = connection.execute(delete(holds).where(holds.c.hold_id == hold_id))
    if gone.rowcount == 1:
        _drop_held(
            connection, client_id, [p for p in row.periods.split(_SEP) if p], row.amount_nano
        )


def _sweep(connection: Connection, client_id: str, before: datetime) -> None:
    """Gives back the reservations of calls that never settled (a process that died)."""
    expired = connection.execute(
        select(holds.c.hold_id).where(holds.c.client_id == client_id, holds.c.created_at < before)
    ).all()
    for row in expired:
        _free(connection, client_id, row.hold_id)


def _count(
    connection: Connection, client_id: str, period: str, limit: int | None, credits: int
) -> None:
    if credits <= 0:
        return
    _ensure_row(connection, client_id, period)
    used = counters.c.consumed_nano
    if limit is None:
        new = used + credits
    else:  # never above the allowance (and never lowered when a smaller plan is already over)
        new = case((used >= limit, used), (used + credits > limit, limit), else_=used + credits)
    connection.execute(
        update(counters)
        .where(counters.c.client_id == client_id, counters.c.period == period)
        .values(consumed_nano=new)
    )


class SqlCreditStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlCreditStore()"

    def reserve(
        self,
        client_id: str,
        hold_id: str,
        amount: int,
        periods: tuple[tuple[str, int | None], ...],
        *,
        now: datetime,
        ttl: timedelta,
    ) -> bool:
        limited = [(p, limit) for p, limit in periods if limit is not None]
        try:
            with self._engine.begin() as connection:
                ensure_client(connection, client_id, created_at=datetime.now(UTC), created_by=None)
                _sweep(connection, client_id, now - ttl)
                for period, limit in limited:
                    _ensure_row(connection, client_id, period)
                    taken = connection.execute(
                        update(counters)
                        .where(
                            counters.c.client_id == client_id,
                            counters.c.period == period,
                            counters.c.consumed_nano + counters.c.held_nano + amount <= limit,
                        )
                        .values(held_nano=counters.c.held_nano + amount)
                    )
                    if taken.rowcount != 1:
                        raise _Short
                connection.execute(
                    insert(holds).values(
                        hold_id=hold_id,
                        client_id=client_id,
                        periods=_SEP.join(p for p, _ in limited),
                        amount_nano=amount,
                        created_at=now,
                    )
                )
            return True
        except _Short:
            return False
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def release(self, client_id: str, hold_id: str) -> None:
        try:
            with self._engine.begin() as connection:
                _free(connection, client_id, hold_id)
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def settle(self, hold: Hold, record: LLMUsageRecord) -> bool:
        try:
            with self._engine.begin() as connection:
                ensure_client(
                    connection, hold.client_id, created_at=datetime.now(UTC), created_by=None
                )
                connection.execute(
                    insert(usage).values(
                        client_id=hold.client_id,
                        occurred_at=record.occurred_at,
                        provider=record.provider,
                        model=record.model,
                        input_tokens=record.input_tokens,
                        output_tokens=record.output_tokens,
                        status=record.status,
                        call_id=record.call_id,
                        operation=record.operation,
                        cost_pico_usd=record.cost_pico_usd,
                        credits_nano=record.credits_nano,
                    )
                )
                _free(connection, hold.client_id, hold.hold_id)
                for period, limit in hold.periods:
                    _count(connection, hold.client_id, period, limit, record.credits_nano or 0)
            return True
        except IntegrityError:  # the same call, settled already: only give the reservation back
            self.release(hold.client_id, hold.hold_id)
            return False
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def consumed(self, client_id: str, periods: tuple[str, ...]) -> dict[str, int]:
        query = select(counters.c.period, counters.c.consumed_nano).where(
            counters.c.client_id == client_id, counters.c.period.in_(periods)
        )
        try:
            with self._engine.connect() as connection:
                return {r[0]: int(r[1]) for r in connection.execute(query)}
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def ledger(self, client_id: str, *, limit: int, offset: int) -> list[LLMUsageRecord]:
        query = (
            select(usage)
            .where(usage.c.client_id == client_id)
            .order_by(usage.c.seq.desc())
            .limit(limit)
            .offset(offset)
        )
        try:
            with self._engine.connect() as connection:
                return [record_of(r) for r in connection.execute(query)]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None


def record_of(r) -> LLMUsageRecord:
    return LLMUsageRecord(
        client_id=r.client_id,
        occurred_at=r.occurred_at,
        provider=r.provider,
        model=r.model,
        status=r.status,
        input_tokens=r.input_tokens,
        output_tokens=r.output_tokens,
        call_id=r.call_id,
        operation=r.operation,
        cost_pico_usd=r.cost_pico_usd,
        credits_nano=r.credits_nano,
    )
