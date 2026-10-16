"""An in-memory write-back ledger: for tests and for a run that needs no memory of its own.

Nothing survives the process. The durable one is `durable.DurableWriteBackLedger`; both pass the same
contract tests (`tests/app/test_writeback_ledger.py`).
"""

from collections.abc import Sequence
from threading import Lock

from sales_app.sourcing.fields import ProspectField
from sales_app.sourcing.writeback.ledger import LedgerEntry, WriteAttempt
from sales_core.contracts import ClientId, ProspectId


class InMemoryWriteBackLedger:
    def __init__(self, client_id: ClientId) -> None:
        if not client_id:
            raise ValueError("a ledger needs a client id")
        self._client_id = client_id
        self._entries: list[LedgerEntry] = []
        self._by_attempt: dict[str, LedgerEntry] = {}
        self._lock = Lock()

    def __repr__(self) -> str:
        return f"InMemoryWriteBackLedger(client={self._client_id!r}, entries={len(self._entries)})"

    @property
    def client_id(self) -> ClientId:
        return self._client_id

    def append(self, attempts: Sequence[WriteAttempt]) -> tuple[LedgerEntry, ...]:
        with self._lock:
            pending: dict[str, WriteAttempt] = {}
            for attempt in attempts:
                if attempt.client_id != self._client_id:
                    raise ValueError("an attempt belongs to another client")
                known = self._by_attempt.get(attempt.attempt_id)
                earlier = known.attempt() if known is not None else pending.get(attempt.attempt_id)
                if earlier is not None and earlier != attempt:
                    raise ValueError("an attempt id is already used by a different attempt")
                if earlier is None:
                    pending[attempt.attempt_id] = attempt
            recorded = dict(self._by_attempt)
            added: list[LedgerEntry] = []
            for attempt_id, attempt in pending.items():
                entry = LedgerEntry(
                    **{name: getattr(attempt, name) for name in _FIELDS},
                    sequence=len(self._entries) + len(added) + 1,
                )
                added.append(entry)
                recorded[attempt_id] = entry
            self._entries.extend(added)
            self._by_attempt = recorded
            return tuple(recorded[a.attempt_id] for a in attempts)

    def entries(self, prospect_id: ProspectId | None = None) -> tuple[LedgerEntry, ...]:
        with self._lock:
            return tuple(
                e for e in self._entries if prospect_id is None or e.prospect_id == prospect_id
            )

    def latest(
        self, prospect_id: ProspectId, field: ProspectField = ProspectField.STATUS
    ) -> LedgerEntry | None:
        with self._lock:
            return next(
                (
                    e
                    for e in reversed(self._entries)
                    if e.prospect_id == prospect_id and e.field is field
                ),
                None,
            )


_FIELDS = (
    "client_id",
    "attempt_id",
    "prospect_id",
    "new",
    "expected",
    "outcome",
    "at",
    "field",
    "code",
    "not_before",
)
