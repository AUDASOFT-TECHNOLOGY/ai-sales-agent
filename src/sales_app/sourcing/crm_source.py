"""A `ProspectSource` over any CRM that can list contacts: the rules, with no vendor in them.

A provider supplies a `CrmReader` (its adapter, with its errors already turned into `SourceError`s)
and the operator's `StatusMapping`; this module does everything the source contract asks of it:

- **Read-only.** `create_prospects` and `update_prospects` raise `SourceReadOnlyError` without
  calling the reader at all, and the capabilities declare nothing writable.
- **Fail closed on records.** A record whose id is outside the accepted alphabet, or whose status is
  blank or not in the operator's mapping, is left out and described by `problems()`; nothing is
  guessed. A do-not-contact signal is never lost: on a kept record the status becomes `OPTED_OUT`,
  on a left-out one the contact is handed to `refused_contacts()` so the consent registry records it.
- **Never a partial list.** A read that fails raises; there is no fallback to anything else.
- **Nothing personal leaks.** Problems are fixed sentences, and the records and prospects here are
  never logged or put in an error or a `repr`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sales_app.sourcing.contacts import contact_keys, lookup_keys
from sales_app.sourcing.errors import SourceReadOnlyError
from sales_app.sourcing.fields import SOURCE_ONLY_FIELDS, ProspectField
from sales_app.sourcing.identifiers import native_id_problem
from sales_app.sourcing.ports import ProspectChange, SourceCapabilities
from sales_app.sourcing.problems import ProblemCode, SourceProblem
from sales_app.sourcing.statuses import StatusMapping, resolve_source_status
from sales_core.contracts import ClientId, ContactInfo, Prospect, ProspectId


@dataclass(frozen=True, slots=True, repr=False)
class CrmRecord:
    """One contact of the CRM, reduced to what a prospect needs. Personal data: its `repr` is empty."""

    native_id: str
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str | None = None  # the CRM's own word, interpreted only through the mapping
    do_not_contact: bool = False
    locale: str | None = None

    def __repr__(self) -> str:
        return "CrmRecord()"


class CrmReader(Protocol):
    """What a CRM provider gives the source. It raises only `SourceError`s, with fixed messages."""

    def list_records(self) -> Sequence[CrmRecord]: ...

    def get_record(self, native_id: str) -> CrmRecord | None: ...

    def find_records(
        self, *, phone: str | None = None, email: str | None = None
    ) -> Sequence[CrmRecord]: ...

    def verify(self) -> list[str]: ...


_DETAILS = {
    ProblemCode.INVALID_NATIVE_ID: "a record has an id outside the accepted alphabet",
    ProblemCode.BLANK_STATUS: "a record carries no status",
    ProblemCode.UNMAPPED_STATUS: "a record has a status the mapping does not list",
}
_READ_ONLY = "the source is read-only"


class CrmProspectSource:
    """Implements `ProspectSource` for one client, read-only."""

    def __init__(
        self, reader: CrmReader, *, client_id: str, mapping: StatusMapping, namespace: str
    ) -> None:
        self._reader = reader
        self._client_id = ClientId(client_id)
        self._mapping = mapping
        # It owns the person and the CRM's status; Revansa's own state stays in the store.
        self._capabilities = SourceCapabilities(
            owned=SOURCE_ONLY_FIELDS | {ProspectField.STATUS},
            id_namespace=namespace,
            consent_signal=True,
        )
        self._problems: dict[tuple[ProblemCode, str | None], SourceProblem] = {}
        self._refused: dict[str, ContactInfo] = {}

    def __repr__(self) -> str:
        return f"CrmProspectSource(client={self._client_id!r})"

    @property
    def client_id(self) -> ClientId:
        return self._client_id

    @property
    def capabilities(self) -> SourceCapabilities:
        return self._capabilities

    # ---- reading ---------------------------------------------------------------------------

    def list_prospects(self) -> Sequence[Prospect]:
        records = self._reader.list_records()
        self._problems, self._refused = {}, {}  # this read describes what is left out now
        return [p for p in (self._interpret(r) for r in records) if p is not None]

    def get_prospect(self, native_id: str) -> Prospect | None:
        if native_id_problem(native_id) is not None:
            return None
        record = self._reader.get_record(native_id)
        return None if record is None else self._interpret(record)

    def find_prospect_by_contact(
        self, *, phone: str | None = None, email: str | None = None
    ) -> Prospect | None:
        wanted = lookup_keys(phone=phone, email=email)
        if not wanted:
            return None
        for record in self._reader.find_records(phone=phone, email=email):
            prospect = self._interpret(record)
            # The CRM's own search may be looser than ours: only a real match is a match.
            if prospect is not None and contact_keys(prospect.contact) & wanted:
                return prospect
        return None

    # ---- writing: never ------------------------------------------------------------------

    def create_prospects(self, prospects: Sequence[Prospect]) -> None:
        raise SourceReadOnlyError(_READ_ONLY)

    def update_prospects(self, changes: Sequence[ProspectChange]) -> None:
        raise SourceReadOnlyError(_READ_ONLY)

    # ---- health ----------------------------------------------------------------------------

    def problems(self) -> tuple[SourceProblem, ...]:
        return tuple(self._problems.values())

    def refused_contacts(self) -> tuple[ContactInfo, ...]:
        return tuple(self._refused.values())

    def verify(self) -> list[str]:
        return self._reader.verify()

    # ---- internals -------------------------------------------------------------------------

    def _interpret(self, record: CrmRecord) -> Prospect | None:
        """The prospect this record stands for, or None (the record is left out and described)."""
        contact = ContactInfo(phone=record.phone, email=record.email)
        if native_id_problem(record.native_id) is not None:
            self._leave_out(ProblemCode.INVALID_NATIVE_ID, None, contact, record.do_not_contact)
            return None
        resolution = resolve_source_status(
            record.status, self._mapping, do_not_contact=record.do_not_contact
        )
        if resolution.status is None:
            assert resolution.problem is not None
            self._leave_out(
                resolution.problem, record.native_id, contact, resolution.opt_out_signal
            )
            return None
        return Prospect(
            client_id=self._client_id,
            prospect_id=ProspectId(record.native_id),
            display_name=record.name or record.email or record.phone or record.native_id,
            contact=contact,
            status=resolution.status,
            locale=record.locale,
        )

    def _leave_out(
        self, code: ProblemCode, native_id: str | None, contact: ContactInfo, refused: bool
    ) -> None:
        self._problems.setdefault((code, native_id), SourceProblem(code, _DETAILS[code], native_id))
        keys = contact_keys(contact)
        if refused and keys:  # a refusal is never lost, even from a record that is itself left out
            self._refused.setdefault("|".join(sorted(keys)), contact)
