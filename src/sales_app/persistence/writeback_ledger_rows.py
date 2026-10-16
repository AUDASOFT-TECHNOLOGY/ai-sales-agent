"""The SQL half of a client's write-back ledger: rows in, rows out, for one client.

Like `operational_rows`, this module knows SQL and nothing of what the rows mean: it moves strings,
numbers and timestamps. Turning them into the write-back's own objects is the job of
`sales_app.sourcing.writeback.durable`, so that SQL stays in this package and the write-back stays
free of it.

Every object is bound to one client and every statement filters on it, so another client's rows
are not reachable through it. `append` is one transaction: a refused call writes nothing. The
entries of a client are numbered 1, 2, 3... without a gap; the number is part of the key, so two
writers racing for the same number cannot both have it, and the loser starts again.

Failures are fixed sentences, never a value or an id. A database that cannot be reached is
`StoreUnavailableError`; an attempt id reused with another content is a `ValueError`.
"""

import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine, func, insert, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import op_writeback_ledger as ledger
from sales_app.sourcing.errors import StoreUnavailableError

Row = Mapping[str, Any]
_UNAVAILABLE = "the write-back ledger is unavailable"
_CONTENT = (
    "prospect_id",
    "field",
    "new_value",
    "expected_value",
    "outcome",
    "code",
    "at",
    "not_before",
)
_TRIES = 4  # a writer that loses the race for a sequence number starts again, a few times


class _LostTheRace(Exception):
    """Another writer took the sequence number: nothing was written, try again."""


class SqlWriteBackLedgerRows:
    def __init__(
        self, engine: Engine, client_id: str, *, now: Callable[[], datetime] | None = None
    ) -> None:
        if not client_id:
            raise ValueError("a ledger needs a client id")
        self._engine = engine
        self._client_id = client_id
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = (
            threading.Lock()
        )  # this process's writers take turns; others are met by the key

    def __repr__(self) -> str:
        return f"SqlWriteBackLedgerRows(client={self._client_id!r})"

    @property
    def client_id(self) -> str:
        return self._client_id

    @contextmanager
    def _read(self) -> Iterator[Connection]:
        try:
            with self._engine.connect() as connection:
                yield connection
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    @contextmanager
    def _write(self) -> Iterator[Connection]:
        """One transaction. Registers the client first (idempotent), so the foreign key holds."""
        try:
            with self._engine.begin() as connection:
                ensure_client(connection, self._client_id, created_at=self._now(), created_by=None)
                yield connection
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def verify(self) -> list[str]:
        try:
            with self._engine.connect() as connection:
                connection.execute(select(ledger.c.client_id).limit(1)).first()
        except SQLAlchemyError:
            return [_UNAVAILABLE]
        return []

    # ---- writing -------------------------------------------------------------------------

    def append(self, rows: Sequence[Row]) -> list[Row]:
        """Record attempts, all or none, and return each as recorded (with its `sequence`), in the
        order given.

        Idempotent by `attempt_id`: an attempt already recorded (or repeated in `rows`) with the
        same content is not recorded again and comes back as it was recorded. The same id with
        another content is a `ValueError`, and nothing is written.
        """
        with self._lock:
            for _ in range(_TRIES):
                try:
                    return self._append_once(rows)
                except _LostTheRace:
                    continue
        raise StoreUnavailableError(_UNAVAILABLE)

    def _append_once(self, rows: Sequence[Row]) -> list[Row]:
        with self._write() as connection:
            known = self._by_attempt(connection, [r["attempt_id"] for r in rows])
            fresh: dict[str, Row] = {}
            for row in rows:
                earlier = known.get(row["attempt_id"]) or fresh.get(row["attempt_id"])
                if earlier is not None:
                    if any(earlier[key] != row[key] for key in _CONTENT):
                        raise ValueError("an attempt id is already used by a different attempt")
                    continue
                fresh[row["attempt_id"]] = row
            if fresh:
                top = connection.execute(
                    select(func.max(ledger.c.sequence)).where(ledger.c.client_id == self._client_id)
                ).scalar()
                sequence = top or 0
                try:
                    for attempt_id, row in fresh.items():
                        sequence += 1
                        values = {key: row[key] for key in ("attempt_id", *_CONTENT)}
                        connection.execute(
                            insert(ledger).values(
                                client_id=self._client_id, sequence=sequence, **values
                            )
                        )
                        known[attempt_id] = {**values, "sequence": sequence}
                except IntegrityError:
                    raise _LostTheRace from None
            return [known[r["attempt_id"]] for r in rows]

    def _by_attempt(self, connection: Connection, attempt_ids: Sequence[str]) -> dict[str, Row]:
        if not attempt_ids:
            return {}
        query = select(ledger).where(
            ledger.c.client_id == self._client_id, ledger.c.attempt_id.in_(list(set(attempt_ids)))
        )
        return {r.attempt_id: _row(r) for r in connection.execute(query)}

    # ---- reading -------------------------------------------------------------------------

    def list_entries(self, prospect_id: str | None = None) -> list[Row]:
        """Every entry of this client, oldest first (by `sequence`), or only those of a prospect."""
        query = select(ledger).where(ledger.c.client_id == self._client_id)
        if prospect_id is not None:
            query = query.where(ledger.c.prospect_id == prospect_id)
        with self._read() as connection:
            return [_row(r) for r in connection.execute(query.order_by(ledger.c.sequence))]

    def latest(self, prospect_id: str, field: str) -> Row | None:
        query = (
            select(ledger)
            .where(
                ledger.c.client_id == self._client_id,
                ledger.c.prospect_id == prospect_id,
                ledger.c.field == field,
            )
            .order_by(ledger.c.sequence.desc())
            .limit(1)
        )
        with self._read() as connection:
            row = connection.execute(query).first()
        return None if row is None else _row(row)


def _row(row: Any) -> Row:
    return {
        "sequence": row.sequence,
        "attempt_id": row.attempt_id,
        "prospect_id": row.prospect_id,
        "field": row.field,
        "new_value": row.new_value,
        "expected_value": row.expected_value,
        "outcome": row.outcome,
        "code": row.code,
        "at": row.at,
        "not_before": row.not_before,
    }
