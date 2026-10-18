"""The SQL side of the daily send cap (the ports of `sales_app.send_cap`).

**The database decides.** A place is taken by one statement (`INSERT … ON CONFLICT DO UPDATE … WHERE
used < limit`): it creates the day's row at 1, or adds one only while fewer than `limit` are used,
and reports whether it changed a row. Two processes can never both take the last place. Counts are
per client, channel and UTC day. A database that cannot be reached is `StoreUnavailableError`, a
fixed sentence: the guard turns it into a refusal, so nothing is sent.
"""

from datetime import UTC, date, datetime

from sqlalchemy import Engine, delete, select, update
from sqlalchemy.exc import SQLAlchemyError

from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import client_send_caps as caps
from sales_app.persistence.tables import op_send_counter as counter
from sales_app.send_cap import check_cap, valid_cap
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the send cap is unavailable"


def _dialect_insert(dialect: str):
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - only the two supported backends
        raise StoreUnavailableError(_UNAVAILABLE)
    return insert


class SqlSendCounter:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlSendCounter()"

    def try_increment(self, client_id: str, channel: str, day: date, limit: int) -> bool:
        if valid_cap(limit) is None:
            return False
        now = datetime.now(UTC)
        try:
            with self._engine.begin() as connection:
                ensure_client(connection, client_id, created_at=now, created_by=None)
                statement = _dialect_insert(connection.dialect.name)(counter).values(
                    client_id=client_id, channel=channel, day=day, used=1, updated_at=now
                )
                result = connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[counter.c.client_id, counter.c.channel, counter.c.day],
                        set_={"used": counter.c.used + 1, "updated_at": now},
                        where=counter.c.used < limit,
                    )
                )
                return result.rowcount == 1
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def decrement(self, client_id: str, channel: str, day: date) -> None:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    update(counter)
                    .where(
                        counter.c.client_id == client_id,
                        counter.c.channel == channel,
                        counter.c.day == day,
                        counter.c.used > 0,
                    )
                    .values(used=counter.c.used - 1, updated_at=datetime.now(UTC))
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def used(self, client_id: str, channel: str, day: date) -> int:
        query = select(counter.c.used).where(
            counter.c.client_id == client_id, counter.c.channel == channel, counter.c.day == day
        )
        try:
            with self._engine.connect() as connection:
                row = connection.execute(query).first()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return int(row[0]) if row else 0


class SqlSendCapStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlSendCapStore()"

    def get(self, client_id: str) -> int | None:
        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    select(caps.c.daily_cap).where(caps.c.client_id == client_id)
                ).first()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return valid_cap(row[0]) if row else None

    def set(self, client_id: str, cap: int, *, by: str, at: datetime) -> None:
        value = check_cap(cap)
        try:
            with self._engine.begin() as connection:
                ensure_client(connection, client_id, created_at=at, created_by=by)
                statement = _dialect_insert(connection.dialect.name)(caps).values(
                    client_id=client_id, daily_cap=value, updated_at=at, updated_by=by
                )
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[caps.c.client_id],
                        set_={
                            "daily_cap": statement.excluded.daily_cap,
                            "updated_at": statement.excluded.updated_at,
                            "updated_by": statement.excluded.updated_by,
                        },
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def remove(self, client_id: str) -> bool:
        try:
            with self._engine.begin() as connection:
                result = connection.execute(delete(caps).where(caps.c.client_id == client_id))
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return bool(result.rowcount)
