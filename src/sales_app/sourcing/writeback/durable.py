"""A write-back ledger that survives a restart: the same contract as the in-memory one, over rows.

It meets exactly what `WriteBackLedger` says (and `tests/app/test_writeback_ledger.py` checks, for
both): appended once per `attempt_id`, all or none, ordered by a per-client sequence without gaps,
bound to one client. What a restart keeps is what the synchroniser needs it for: how long to wait
after a failure, which writes a person has overruled, and which value in a next-action cell is
Revansa's own.

The rows layer is `sales_app.persistence.writeback_ledger_rows.SqlWriteBackLedgerRows`: it moves
strings, numbers and timestamps and knows nothing of the objects here, and this module knows nothing
of SQL. A stored row that cannot be read back is a `StoreError`, never a guess.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from sales_app.sourcing.errors import StoreError
from sales_app.sourcing.fields import ProspectField
from sales_app.sourcing.writeback.ledger import (
    FailureCode,
    LedgerEntry,
    Outcome,
    WriteAttempt,
)
from sales_core.contracts import ClientId, ProspectId, ProspectStatus

Row = Mapping[str, Any]
_UNREADABLE = "a stored attempt could not be read"


class LedgerRows(Protocol):
    """The rows layer this ledger talks to (`SqlWriteBackLedgerRows`): primitives only."""

    @property
    def client_id(self) -> str: ...
    def verify(self) -> list[str]: ...
    def append(self, rows: Sequence[Row]) -> list[Row]: ...
    def list_entries(self, prospect_id: str | None = None) -> list[Row]: ...
    def latest(self, prospect_id: str, field: str) -> Row | None: ...


class DurableWriteBackLedger:
    """Implements `WriteBackLedger` for one client over a rows layer."""

    def __init__(self, rows: LedgerRows) -> None:
        if not rows.client_id:
            raise ValueError("a ledger needs a client id")
        self._rows = rows
        self._client_id = ClientId(rows.client_id)

    def __repr__(self) -> str:
        return f"DurableWriteBackLedger(client={self._client_id!r})"

    @property
    def client_id(self) -> ClientId:
        return self._client_id

    def verify(self) -> list[str]:
        return self._rows.verify()

    def append(self, attempts: Sequence[WriteAttempt]) -> tuple[LedgerEntry, ...]:
        for attempt in attempts:
            if attempt.client_id != self._client_id:
                raise ValueError("an attempt belongs to another client")
        recorded = self._rows.append([_to_row(a) for a in attempts])
        return tuple(self._entry(row) for row in recorded)

    def entries(self, prospect_id: ProspectId | None = None) -> tuple[LedgerEntry, ...]:
        return tuple(self._entry(r) for r in self._rows.list_entries(prospect_id))

    def latest(
        self, prospect_id: ProspectId, field: ProspectField = ProspectField.STATUS
    ) -> LedgerEntry | None:
        row = self._rows.latest(prospect_id, field.value)
        return None if row is None else self._entry(row)

    def _entry(self, row: Row) -> LedgerEntry:
        try:
            field = ProspectField(row["field"])
            return LedgerEntry(
                client_id=self._client_id,
                attempt_id=row["attempt_id"],
                prospect_id=ProspectId(row["prospect_id"]),
                new=_value(field, row["new_value"]),
                expected=_value(field, row["expected_value"]),
                outcome=Outcome(row["outcome"]),
                at=row["at"],
                field=field,
                code=None if row["code"] is None else FailureCode(row["code"]),
                not_before=row["not_before"],
                sequence=row["sequence"],
            )
        except (KeyError, ValueError, TypeError):
            raise StoreError(_UNREADABLE) from None


def _to_row(attempt: WriteAttempt) -> Row:
    return {
        "attempt_id": attempt.attempt_id,
        "prospect_id": str(attempt.prospect_id),
        "field": attempt.field.value,
        "new_value": str(attempt.new),
        "expected_value": str(attempt.expected),
        "outcome": attempt.outcome.value,
        "code": None if attempt.code is None else attempt.code.value,
        "at": attempt.at,
        "not_before": attempt.not_before,
    }


def _value(field: ProspectField, text: str) -> ProspectStatus | str:
    return ProspectStatus(text) if field is ProspectField.STATUS else text
