"""The SQL positions of one client's mailboxes (the port of `sales_integrations.mail.cursor`).

Bound to one client: every statement filters on it. A position holds a mailbox's digest (never its
address), a UID, the UIDVALIDITY it belongs to and a date: nothing of any message. It trades plain
values with the reader (`(uidvalidity, next_uid, updated_at)`), because the database may not import
the adapter.

**It only moves forward.** A save replaces the row only if the UIDVALIDITY differs (the old UIDs
mean nothing) or the new position is further on, and the database itself decides, in one statement:
two writers cannot move it backwards, whatever the order they arrive in. A database that cannot be
reached is `StoreUnavailableError`, a fixed sentence (the reader turns any failure of its store into
its own): it then does not read, rather than lose a message or read it twice.
"""

from datetime import UTC, datetime

from sqlalchemy import Engine, or_, select
from sqlalchemy.exc import SQLAlchemyError

from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import op_mail_cursor as cursors
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the mailbox's reading position is unavailable"


class SqlMailCursorStore:
    def __init__(self, engine: Engine, client_id: str) -> None:
        if not client_id:
            raise ValueError("a position store needs a client id")
        self._engine = engine
        self._client_id = client_id

    def __repr__(self) -> str:
        return f"SqlMailCursorStore(client={self._client_id!r})"

    def load(self, mailbox: str) -> tuple[int, int, datetime] | None:
        query = select(cursors).where(
            cursors.c.client_id == self._client_id, cursors.c.mailbox_key == mailbox
        )
        try:
            with self._engine.connect() as connection:
                row = connection.execute(query).first()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        if row is None:
            return None
        return int(row.uid_validity), int(row.next_uid), row.updated_at

    def save(self, mailbox: str, uidvalidity: int, next_uid: int, updated_at: datetime) -> None:
        values = {
            "client_id": self._client_id,
            "mailbox_key": mailbox,
            "uid_validity": uidvalidity,
            "next_uid": next_uid,
            "updated_at": updated_at,
        }
        try:
            with self._engine.begin() as connection:
                ensure_client(
                    connection, self._client_id, created_at=datetime.now(UTC), created_by=None
                )
                insert = _dialect_insert(connection.dialect.name)
                statement = insert(cursors).values(**values)
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[cursors.c.client_id, cursors.c.mailbox_key],
                        set_={
                            "uid_validity": statement.excluded.uid_validity,
                            "next_uid": statement.excluded.next_uid,
                            "updated_at": statement.excluded.updated_at,
                        },
                        where=or_(
                            cursors.c.uid_validity != statement.excluded.uid_validity,
                            cursors.c.next_uid < statement.excluded.next_uid,
                        ),
                    )
                )
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None


def _dialect_insert(dialect: str):
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - only the two supported backends
        raise StoreUnavailableError(_UNAVAILABLE)
    return insert
