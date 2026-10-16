"""The write-back ledger: what Revansa tried to write into a source, and how each try ended.

A **journal of attempts, not the source of truth**. What to write is always recomputed from the
current state of the source and of Revansa (see `planning`), so losing the ledger loses no intent. It
records what happened so that the synchroniser can back off after a failure, stop retrying a write a
person has overruled, and leave a trail for the operator.

An entry holds only: the client, the prospect's public id (an opaque CRM id), the field, the two
values of the write (a status, or for the other fields a short canonical text: a date, a count, a
next action; see `planning.value_text`), the outcome with a fixed failure code, and times. Never a name, a
contact, a message or anything the source or its service said. The id of a local prospect (it embeds
the sender's number or address) is refused: a local prospect is never written back. The caller makes
`attempt_id` out of ids and counters, never out of a contact.

Entries are appended, never rewritten or removed by this port. Like every port of a client's data,
a ledger is bound to one client.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sales_app.sourcing.fields import ProspectField
from sales_app.sourcing.identifiers import is_local_id
from sales_app.sourcing.writeback.policy import WRITEBACK_FIELDS
from sales_core.contracts import ClientId, ProspectId, ProspectStatus

ATTEMPT_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,128}")  # applied with `fullmatch`
MAX_VALUE_CHARS = 255  # a canonical value is a date, a count or a type and a date: never longer

# What a write replaces and puts: the status itself for the status, a canonical text for the rest.
LedgerValue = ProspectStatus | str


class Outcome(StrEnum):
    APPLIED = "applied"  # the source accepted the write
    CONFLICT = "conflict"  # the source's value had changed since it was read: nothing was written
    DEFERRED = "deferred"  # the source could not be reached or asked to wait: try again later
    FAILED = "failed"  # refused for a reason that waiting does not cure


class FailureCode(StrEnum):
    """Why a write did not happen. Fixed codes: never what the source said."""

    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    AUTH = "auth"
    READ_ONLY = "read_only"
    UNEXPECTED = "unexpected"


_DEFERRED_CODES = frozenset({FailureCode.UNAVAILABLE, FailureCode.RATE_LIMITED})
_FAILED_CODES = frozenset({FailureCode.AUTH, FailureCode.READ_ONLY, FailureCode.UNEXPECTED})


@dataclass(frozen=True, slots=True, kw_only=True)
class WriteAttempt:
    """One try at one write, as the synchroniser reports it.

    - `attempt_id` identifies the try: recording it twice records it once (a retry after a failure
      of unknown outcome cannot duplicate it).
    - `new` and `expected` are the values of the planned write (`expected` is what the source held
      when it was read): a `ProspectStatus` for the status, a canonical text (possibly empty: an
      empty cell) for any other field.
    - `code` is set exactly when the outcome is `DEFERRED` or `FAILED`; `not_before` may only
      accompany `DEFERRED`: the earliest time to try again, when the source said.
    """

    client_id: ClientId
    attempt_id: str
    prospect_id: ProspectId  # the public id the Core sees
    new: LedgerValue
    expected: LedgerValue
    outcome: Outcome
    at: datetime
    field: ProspectField = ProspectField.STATUS
    code: FailureCode | None = None
    not_before: datetime | None = None

    def __post_init__(self) -> None:
        if not self.client_id:
            raise ValueError("an attempt needs a client id")
        if not ATTEMPT_ID_PATTERN.fullmatch(self.attempt_id):
            raise ValueError("an attempt id is 1 to 128 letters, digits and . _ : -")
        if not self.prospect_id:
            raise ValueError("an attempt needs a prospect id")
        if is_local_id(self.prospect_id):
            raise ValueError("a local prospect is never written back")
        if self.field not in WRITEBACK_FIELDS:
            raise ValueError("this field is never written back")
        for value in (self.new, self.expected):
            if not isinstance(value, str) or (self.field is ProspectField.STATUS) != isinstance(
                value, ProspectStatus
            ):
                raise ValueError("a value does not fit the field it is recorded for")
            if len(value) > MAX_VALUE_CHARS:
                raise ValueError("a value is too long to be recorded")
        if self.new == self.expected:
            raise ValueError("a write changes the field: new and expected differ")
        for name in ("at", "not_before"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.outcome is Outcome.DEFERRED:
            allowed = _DEFERRED_CODES
        elif self.outcome is Outcome.FAILED:
            allowed = _FAILED_CODES
        else:
            allowed = frozenset()
        if (self.code is None) != (not allowed) or (
            self.code is not None and self.code not in allowed
        ):
            raise ValueError("the failure code does not fit the outcome")
        if self.not_before is not None and self.outcome is not Outcome.DEFERRED:
            raise ValueError("only a deferred attempt has a time to try again")


@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerEntry(WriteAttempt):
    """A recorded attempt. `sequence` is its place in the ledger: 1 for the first, then 2, 3...
    with no gap, and it is what orders entries (never the time, which a caller may get wrong)."""

    sequence: int

    def __post_init__(self) -> None:
        WriteAttempt.__post_init__(self)
        if self.sequence < 1:
            raise ValueError("a sequence starts at 1")

    def attempt(self) -> WriteAttempt:
        return WriteAttempt(
            client_id=self.client_id,
            attempt_id=self.attempt_id,
            prospect_id=self.prospect_id,
            new=self.new,
            expected=self.expected,
            outcome=self.outcome,
            at=self.at,
            field=self.field,
            code=self.code,
            not_before=self.not_before,
        )


@runtime_checkable
class WriteBackLedger(Protocol):
    """Append-only journal of write-back attempts, for one client."""

    @property
    def client_id(self) -> ClientId: ...

    def append(self, attempts: Sequence[WriteAttempt]) -> tuple[LedgerEntry, ...]:
        """Record attempts, all or none, and return each as recorded, in the order given.

        Idempotent by `attempt_id`: an attempt already recorded (or repeated in `attempts`) with the
        same content is not recorded again and comes back as it was recorded. The same id with a
        different content is refused with `ValueError`, writing nothing, like an attempt of another
        client.
        """
        ...

    def entries(self, prospect_id: ProspectId | None = None) -> tuple[LedgerEntry, ...]:
        """Every entry, oldest first (by `sequence`), or only those of one prospect."""
        ...

    def latest(
        self, prospect_id: ProspectId, field: ProspectField = ProspectField.STATUS
    ) -> LedgerEntry | None:
        """The newest entry for this prospect and field, or None."""
        ...
