"""A `ProspectSource` over a spreadsheet of people: the rules, with no Google in them.

A provider supplies a `SheetsReader` (its adapter, reading only the prospects tab) and this module
does everything the source contract asks of it. It is the **reading half** of what the Google Sheets
adapter does today when one spreadsheet is both source and store:

- **People only.** It owns what a person is (name, contact, channel, language) and the status a
  human writes in the sheet, so a person marked `opted_out` or `rejected` there is never contacted.
  Everything Revansa itself keeps (stage, schedule, conversation, Telegram chat) is **not** owned:
  whatever the sheet holds in those columns is ignored here and set to a neutral value, and the
  operational store is where it lives. Actions, messages, facts and the audit trail never come from
  here.
- **Read-only, unless the operator turned write-back on.** By default `create_prospects` and
  `update_prospects` raise `SourceReadOnlyError` without touching the reader, and the capabilities
  declare nothing writable. A source built with `writable` fields (its provider has checked that
  each has a column the operator named) accepts `update_prospects` for those fields only, never
  creates a prospect, and never writes what a person is. The status is the sheet's own (owned);
  the other writable fields are **mirrored**: Revansa's own bookkeeping, copied into a column, never
  read back as the truth. A write only goes out if the cell still holds what the caller read, but
  this is not an atomic compare-and-set (Google Sheets has none): a person's edit between the check
  and the write can still be overwritten, within the time of one API round trip.
- **Ids.** With a namespace the ids are `<namespace>:<row id>`, and a row whose id is outside the
  accepted alphabet is left out and reported. With none (a client's historical spreadsheet) the ids
  are the sheet's own, untouched and unvalidated, exactly as they have always been.
- **Fail closed, never a partial list.** A row that cannot be interpreted is left out and described
  by `problems()`; a read that fails raises.
- **Nothing personal leaks.** Problems are fixed sentences; records are never logged or put in an
  error or a `repr`.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from sales_app.sourcing.errors import SourceReadOnlyError
from sales_app.sourcing.fields import SOURCE_ONLY_FIELDS, ProspectField
from sales_app.sourcing.identifiers import check_namespace, native_id_problem
from sales_app.sourcing.ports import ProspectChange, SourceCapabilities
from sales_app.sourcing.problems import ProblemCode, SourceProblem
from sales_core.contracts import ClientId, ContactInfo, Prospect, Scheduling


class RowIssue(StrEnum):
    """Why the adapter left a row of the prospects tab out."""

    DUPLICATE_ID = "duplicate_id"  # the same id on several rows: all of them are left out
    DUPLICATE_PHONE = "duplicate_phone"  # the same number as an earlier row: this one is left out
    UNREADABLE = "unreadable"  # a cell that cannot be interpreted (a date, a status, a channel)


@dataclass(frozen=True, slots=True)
class RowProblem:
    """One row of the prospects tab that was left out. `row` is its line number in the sheet."""

    row: int
    issue: RowIssue


class SheetsReader(Protocol):
    """What a spreadsheet provider gives the source. It raises only `SourceError`s, with fixed
    messages, and never writes."""

    def list_prospects(self) -> Sequence[Prospect]: ...

    def get_prospect(self, native_id: str) -> Prospect | None: ...

    def find_prospect_by_contact(
        self, *, phone: str | None = None, email: str | None = None
    ) -> Prospect | None: ...

    def row_problems(self) -> Sequence[RowProblem]:
        """The rows left out by the latest reads."""
        ...

    def verify(self) -> list[str]: ...


@runtime_checkable
class SheetsWriter(Protocol):
    """What a spreadsheet provider adds when write-back is on. Like the reader, it raises only
    `SourceError`s (or `KeyError` for an id that is not on the sheet), with fixed messages."""

    def read_writeback_values(
        self, fields: Collection[ProspectField]
    ) -> Mapping[str, Mapping[ProspectField, Any]]:
        """What the sheet holds now in the mirrored columns of these fields, by native id."""
        ...

    def write_fields(self, changes: Sequence[ProspectChange]) -> None:
        """Write the named fields of the named rows, each only if it still holds its expected
        value; all or nothing for the call. Raises `SourceConflictError` otherwise."""
        ...


_ROW_DETAILS = {
    RowIssue.DUPLICATE_ID: (
        ProblemCode.DUPLICATE_ID,
        "the same id is on several rows of the prospects tab: none of them is read",
    ),
    RowIssue.DUPLICATE_PHONE: (
        ProblemCode.DUPLICATE_CONTACT,
        "a row of the prospects tab has the phone number of an earlier row: it is not read",
    ),
    RowIssue.UNREADABLE: (
        ProblemCode.UNREADABLE_RECORD,
        "a row of the prospects tab cannot be interpreted: it is not read",
    ),
}
_INVALID_ID = "a row has an id outside the accepted alphabet"
_READ_ONLY = "the source is read-only"
_FIELD_REFUSED = "a field of this change cannot be written"
_UNKNOWN = "unknown prospect"


class SheetsProspectSource:
    """Implements `ProspectSource` for one client and one spreadsheet, read-only."""

    def __init__(
        self,
        reader: SheetsReader,
        *,
        client_id: str,
        namespace: str | None,
        writable: Collection[ProspectField] = (),
    ) -> None:
        self._reader = reader
        self._client_id = ClientId(client_id)
        self._namespace = check_namespace(namespace)
        self._writable = frozenset(writable)
        if self._writable and not isinstance(reader, SheetsWriter):
            raise ValueError("this reader cannot write to a sheet")
        # A person's status is also what the sheet's owner writes there: an opt-out typed in the
        # sheet binds, while Revansa's own lifecycle stays in the store. What Revansa mirrors in a
        # column is not owned: the store stays the truth.
        self._capabilities = SourceCapabilities(
            owned=SOURCE_ONLY_FIELDS | {ProspectField.STATUS},
            writable=self._writable,
            mirrored=self._writable - {ProspectField.STATUS},
            id_namespace=namespace,
            consent_signal=ProspectField.STATUS in self._writable,
        )
        self._invalid_ids = 0

    def __repr__(self) -> str:
        return f"SheetsProspectSource(client={self._client_id!r})"

    @property
    def client_id(self) -> ClientId:
        return self._client_id

    @property
    def capabilities(self) -> SourceCapabilities:
        return self._capabilities

    # ---- reading ---------------------------------------------------------------------------

    def list_prospects(self) -> Sequence[Prospect]:
        rows = self._reader.list_prospects()
        kept = [row for row in rows if self._acceptable(row.prospect_id)]
        self._invalid_ids = len(rows) - len(kept)  # this read describes what is left out now
        return [self._person(row) for row in kept]

    def get_prospect(self, native_id: str) -> Prospect | None:
        if not self._acceptable(native_id):
            return None
        row = self._reader.get_prospect(native_id)
        return None if row is None else self._person(row)

    def find_prospect_by_contact(
        self, *, phone: str | None = None, email: str | None = None
    ) -> Prospect | None:
        row = self._reader.find_prospect_by_contact(phone=phone, email=email)
        if row is None or not self._acceptable(row.prospect_id):
            return None
        return self._person(row)

    def read_writeback_values(self) -> Mapping[str, Mapping[ProspectField, Any]]:
        """What the sheet holds now in the mirrored columns (see `WriteBackReadable`); nothing, and
        no request, if write-back is off."""
        mirrored = self._capabilities.mirrored
        if not mirrored:
            return {}
        assert isinstance(self._reader, SheetsWriter)
        found = self._reader.read_writeback_values(mirrored)
        return {native: values for native, values in found.items() if self._acceptable(native)}

    # ---- writing: only what the operator turned on -----------------------------------------

    def create_prospects(self, prospects: Sequence[Prospect]) -> None:
        raise SourceReadOnlyError(_READ_ONLY)

    def update_prospects(self, changes: Sequence[ProspectChange]) -> None:
        """Refused, before any request, unless write-back is on and every field is one of its own."""
        if not self._writable:
            raise SourceReadOnlyError(_READ_ONLY)
        for change in changes:
            if not set(change.new) <= self._writable:
                raise SourceReadOnlyError(_FIELD_REFUSED)
            if not self._acceptable(change.prospect_id):
                raise KeyError(_UNKNOWN)
        if changes:
            assert isinstance(self._reader, SheetsWriter)
            self._reader.write_fields(changes)

    # ---- health ----------------------------------------------------------------------------

    def problems(self) -> tuple[SourceProblem, ...]:
        found = [
            SourceProblem(*_ROW_DETAILS[problem.issue]) for problem in self._reader.row_problems()
        ]
        if self._invalid_ids:
            found.append(SourceProblem(ProblemCode.INVALID_NATIVE_ID, _INVALID_ID))
        return tuple(dict.fromkeys(found))  # one line per kind of problem, in a stable order

    def refused_contacts(self) -> tuple[ContactInfo, ...]:
        """None: a row left out is never contacted, and the sheet has no do-not-contact signal
        beyond the status of a row that is read."""
        return ()

    def verify(self) -> list[str]:
        return self._reader.verify()

    # ---- internals -------------------------------------------------------------------------

    def _acceptable(self, native_id: str) -> bool:
        """Only a namespaced id is held to the alphabet; a historical id is kept whatever it is."""
        return self._namespace is None or native_id_problem(native_id) is None

    def _person(self, row: Prospect) -> Prospect:
        """The person, without the columns that are Revansa's own (neutral values, ignored)."""
        return replace(
            row,
            stage=None,
            conversation_id=None,
            schedule=Scheduling(),
            contact=replace(row.contact, telegram_chat_id=None),
            lane_id=None,
        )
