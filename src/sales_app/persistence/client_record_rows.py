"""The SQL `ClientRecordStore`: identity, status and engine in the `clients` table."""

from datetime import datetime

from sqlalchemy import Engine, select, update

from sales_app.client_records import ClientRecord
from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import clients

_FIELDS = ("display_name", "enabled", "engine")


def _record(row) -> ClientRecord:
    return ClientRecord(row.client_id, row.display_name, row.enabled, row.engine)


class SqlClientRecordStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, client_id: str) -> ClientRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(clients).where(clients.c.client_id == client_id)
            ).first()
        return None if row is None else _record(row)

    def list(self) -> list[ClientRecord]:
        with self._engine.connect() as connection:
            rows = connection.execute(select(clients).order_by(clients.c.client_id)).all()
        return [_record(row) for row in rows]

    def save(self, record: ClientRecord, *, at: datetime, by: str) -> None:
        values = {
            name: getattr(record, name) for name in _FIELDS if getattr(record, name) is not None
        }
        with self._engine.begin() as connection:
            ensure_client(connection, record.client_id, created_at=at, created_by=by)
            if values:
                connection.execute(
                    update(clients)
                    .where(clients.c.client_id == record.client_id)
                    .values(**values, updated_at=at, updated_by=by)
                )

    def set_enabled(self, client_id: str, enabled: bool, *, at: datetime, by: str) -> None:
        self.save(ClientRecord(client_id, enabled=enabled), at=at, by=by)
