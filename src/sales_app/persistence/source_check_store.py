"""The last source test of each client in SQL: the `SourceCheckStore` port."""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import client_source_check
from sales_app.source_checks import SourceCheck, SourceCheckStoreUnavailableError


class SqlSourceCheckStore:
    def __init__(self, engine: Engine, *, now: Callable[[], datetime] | None = None) -> None:
        self._engine = engine
        self._now = now or (lambda: datetime.now(UTC))

    def get(self, client_id: str) -> SourceCheck | None:
        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    select(client_source_check).where(client_source_check.c.client_id == client_id)
                ).first()
        except SQLAlchemyError:
            raise SourceCheckStoreUnavailableError(
                "the source check store is unavailable"
            ) from None
        return None if row is None else _check(row)

    def set(self, client_id: str, *, fingerprint: str, ok: bool, at: datetime) -> SourceCheck:
        check = SourceCheck(client_id, fingerprint, ok, at)
        values = {"fingerprint": fingerprint, "ok": ok, "checked_at": at}
        try:
            try:
                self._write(client_id, values)
            except IntegrityError:  # two first writes raced: the second one now updates
                self._write(client_id, values)
        except SQLAlchemyError:
            raise SourceCheckStoreUnavailableError(
                "the source check store is unavailable"
            ) from None
        return check

    def _write(self, client_id: str, values: dict) -> None:
        with self._engine.begin() as connection:
            ensure_client(connection, client_id, created_at=self._now(), created_by=None)
            changed = connection.execute(
                update(client_source_check)
                .where(client_source_check.c.client_id == client_id)
                .values(**values)
            ).rowcount
            if not changed:
                connection.execute(
                    insert(client_source_check).values(client_id=client_id, **values)
                )


def _check(row) -> SourceCheck:
    return SourceCheck(
        client_id=row.client_id,
        fingerprint=row.fingerprint,
        ok=row.ok,
        checked_at=row.checked_at,
    )
