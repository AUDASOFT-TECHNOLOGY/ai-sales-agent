"""The SQL side of `sales_app.conversation_control`: who is in charge of each conversation.

Bound to nothing: every statement names the client, and the layer above is bound to one client, so a
value sent by a browser can never reach another client's rows. A database that cannot be reached is
`StoreUnavailableError`, a fixed sentence that repeats no id: the Engine reads that as "a person may
be in charge" and sends nothing.
"""

from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError

from sales_app.conversation_control import ControlRecord
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import conversation_control as table
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the conversation control is unavailable"
HUMAN = "human"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _record(row) -> ControlRecord:
    return ControlRecord(
        client_id=row.client_id,
        prospect_id=row.prospect_id,
        mode=row.mode,
        taken_by=row.taken_by,
        taken_at=_aware(row.taken_at),
        reason=row.reason,
        released_at=_aware(row.released_at),
    )


class SqlControlRows:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlControlRows()"

    def get(self, client_id: str, prospect_id: str) -> ControlRecord | None:
        query = select(table).where(
            table.c.client_id == client_id, table.c.prospect_id == prospect_id
        )
        try:
            with self._engine.connect() as connection:
                row = connection.execute(query).first()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return None if row is None else _record(row)

    def humans(self, client_id: str) -> frozenset[str]:
        query = select(table.c.prospect_id).where(
            table.c.client_id == client_id, table.c.mode == HUMAN
        )
        try:
            with self._engine.connect() as connection:
                return frozenset(connection.execute(query).scalars())
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def put(self, record: ControlRecord) -> None:
        values = {
            "mode": record.mode,
            "taken_by": record.taken_by,
            "taken_at": record.taken_at,
            "reason": record.reason,
            "released_at": record.released_at,
        }
        try:
            with self._engine.begin() as connection:
                ensure_client(
                    connection, record.client_id, created_at=datetime.now(UTC), created_by=None
                )
                found = connection.execute(
                    table.update()
                    .where(
                        table.c.client_id == record.client_id,
                        table.c.prospect_id == record.prospect_id,
                    )
                    .values(**values)
                )
                if found.rowcount == 0:
                    connection.execute(
                        table.insert().values(
                            client_id=record.client_id, prospect_id=record.prospect_id, **values
                        )
                    )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
