"""One `SalesRepository` made of a prospect source, an operational store and the consent registry.

The Core asks for a repository and knows nothing of how it is assembled. This module is that
assembly for a client whose prospects live in a CRM (or any `ProspectSource`) and whose own data
lives in an `OperationalStore`. A client whose spreadsheet plays both roles does **not** go through
here: `GoogleSheetsRepository` is used directly, unchanged.

**Reads** join what the source says a person is with what Revansa holds about them (`merge`), and
treat a contact the consent registry blocks as `OPTED_OUT` whichever record it is found on. The
registry is read once per read (`snapshot()`); the refusals a source reports for records it left
out are recorded in it first (`refused_contacts()`), so they bind at once. A store that fails fails
the read: answering from the source alone would hide the pauses and closures only the store knows.

**Writes never reach the source.** Revansa's own fields go to the store, a prospect it creates is
local, and a change to a field the source owns is refused (`SourceReadOnlyError`) rather than
dropped. Everything is checked before the first write, so a refused batch writes nothing.

**Consent.** An opt-out is recorded in the registry only on an explicit signal: the Engine's audit
event for an opt-out keyword. A status change to `OPTED_OUT` whose cause is unknown is never turned
into a registry entry with an invented origin.

Nothing here logs, prints or puts a prospect's id, name, number or address in an error message or a
`repr`: a local id embeds the sender's number or address.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from sales_app.sourcing.consent import (
    ConsentError,
    ConsentWriter,
    Origin,
    ReasonCode,
    RegistrySnapshot,
    record_refusals,
)
from sales_app.sourcing.contacts import contact_keys
from sales_app.sourcing.errors import (
    InvalidNativeIdError,
    SourceReadOnlyError,
    StateConflictError,
)
from sales_app.sourcing.fields import (
    SOURCE_ONLY_FIELDS,
    ProspectField,
    changed_fields,
)
from sales_app.sourcing.identifiers import local_id_problem, to_native_id, to_public_id
from sales_app.sourcing.merge import merge_prospect
from sales_app.sourcing.ports import OperationalStore, ProspectSource, ProspectState
from sales_app.sourcing.problems import ProblemCode, SourceProblem
from sales_app.sourcing.statuses import effective_status
from sales_core.contracts import (
    ActionStatus,
    AuditEvent,
    ClientId,
    ContactInfo,
    Conversation,
    Fact,
    Message,
    Prospect,
    ProspectId,
    ProspectStatus,
    SalesAction,
    Scheduling,
)

# The audit event the Engine emits when an inbound message is an opt-out keyword (and only then):
# `sales_core.engine.events.PROSPECT_OPTED_OUT`. A test keeps the two equal.
KEYWORD_OPT_OUT_EVENT = "prospect.opted_out"
KEYWORD_ACTOR = "system:inbound"

_UNKNOWN = "unknown prospect"


class _Gate:
    """Who is blocked during one read: the registry's snapshot, plus the refusals this read added."""

    __slots__ = ("_extra", "_snapshot")

    def __init__(self, snapshot: RegistrySnapshot, extra: frozenset[str]) -> None:
        self._snapshot = snapshot
        self._extra = extra

    def blocks(self, contact: ContactInfo) -> bool:
        return self._snapshot.blocks(contact) or bool(contact_keys(contact) & self._extra)


@dataclass(frozen=True, slots=True)
class _Change:
    """A validated update: which state fields of which prospect take the incoming values."""

    prospect_id: ProspectId
    incoming: Prospect
    fields: frozenset[ProspectField]


class CompositeRepository:
    """Implements `SalesRepository`, `FactsReader` and `FactsWriter` for one client."""

    def __init__(
        self,
        source: ProspectSource,
        store: OperationalStore,
        consent: ConsentWriter,
    ) -> None:
        if source.client_id != store.client_id:
            raise ValueError("the source and the store belong to different clients")
        self._source = source
        self._store = store
        self._consent = consent
        self._namespace = source.capabilities.id_namespace
        self._conflicts: tuple[SourceProblem, ...] = ()

    def __repr__(self) -> str:
        return f"CompositeRepository(client={self.client_id!r})"

    @property
    def client_id(self) -> ClientId:
        return self._store.client_id

    # ---- reading prospects ---------------------------------------------------------------

    def list_prospects(self) -> Sequence[Prospect]:
        found = self._source.list_prospects()
        local = list(self._store.list_local_prospects())
        states = self._store.list_states()
        gate = self._gate(read_source=True)

        owned = self._source.capabilities.owned
        result: list[Prospect] = []
        public_ids: set[str] = set()
        contacts: set[str] = set()
        for record in found:
            public = self._public_id(record.prospect_id)
            if public is None:
                continue
            public_ids.add(public)
            contacts |= contact_keys(record.contact)
            result.append(
                merge_prospect(
                    record,
                    states.get(ProspectId(public)),
                    owned=owned,
                    public_id=public,
                    contact_opted_out=gate.blocks(record.contact),
                )
            )
        conflicts: list[SourceProblem] = []
        for prospect in local:
            if prospect.prospect_id in public_ids:  # the source wins; the local one is set aside
                conflicts.append(_conflict("a local prospect has the id of a source record"))
                continue
            if contact_keys(prospect.contact) & contacts:
                conflicts.append(_conflict("a local prospect and a source record share a contact"))
            result.append(self._local_view(prospect, gate))
        self._conflicts = tuple(conflicts)
        return result

    def get_prospect(self, prospect_id: ProspectId) -> Prospect | None:
        native = self._native_id(prospect_id)
        gate = self._gate(read_source=native is not None)
        if native is not None:
            record = self._source.get_prospect(native)
            if record is not None:
                return self._merged(record, prospect_id, gate)
        local = self._store.get_local_prospect(prospect_id)
        return self._local_view(local, gate) if local is not None else None

    def find_prospect_by_contact(
        self, *, phone: str | None = None, email: str | None = None
    ) -> Prospect | None:
        record = self._source.find_prospect_by_contact(phone=phone, email=email)
        if record is not None:
            public = self._public_id(record.prospect_id)
            if public is not None:
                return self._merged(record, ProspectId(public), self._gate(read_source=True))
        local = self._store.find_local_by_contact(phone=phone, email=email)
        if local is None:
            return None
        return self._local_view(local, self._gate(read_source=record is not None))

    # ---- writing prospects ---------------------------------------------------------------

    def create_prospects(self, prospects: Sequence[Prospect]) -> None:
        """Local prospects only: the source is never asked to create one."""
        if not prospects:
            return
        for prospect in prospects:
            self._require_client(prospect.client_id)
            if local_id_problem(prospect.prospect_id) is not None:
                raise ValueError("the id cannot be held safely by the store")
            native = self._native_id(prospect.prospect_id)
            if native is not None and self._source.get_prospect(native) is not None:
                raise ValueError("a prospect with this id already exists")
            if self._source.find_prospect_by_contact(
                phone=prospect.contact.phone, email=prospect.contact.email
            ):
                raise ValueError("the contact is already in use")
        gate = self._gate(read_source=True)
        # A person who refused is never recreated as contactable, even if their record was left out.
        self._store.create_local_prospects(
            [
                replace(p, status=ProspectStatus.OPTED_OUT) if gate.blocks(p.contact) else p
                for p in prospects
            ]
        )

    def update_prospects(self, prospects: Sequence[Prospect]) -> None:
        """Save Revansa's own fields to the store, checking everything before writing anything."""
        incoming = {p.prospect_id: p for p in prospects}
        changes = [self._validated(p) for p in incoming.values()]
        changes = [c for c in changes if c.fields]
        if not changes:
            return
        try:
            self._store.save_states(self._new_states(changes))
        except StateConflictError:  # someone changed a state since it was read: once more, fresh
            self._store.save_states(self._new_states(changes))

    # ---- facts ---------------------------------------------------------------------------

    def get_facts(self, prospect_id: ProspectId) -> Sequence[Fact]:
        return self._store.get_facts(prospect_id)

    def append_facts(self, prospect_id: ProspectId, facts: Sequence[Fact]) -> None:
        """The store accepts facts for any id, so existence is checked here, before the write."""
        if self._find_raw(prospect_id) is None:
            raise KeyError(_UNKNOWN)
        self._store.append_facts(prospect_id, facts)

    # ---- the rest of the repository: the store's own ---------------------------------------

    def list_actions(
        self, statuses: Collection[ActionStatus] | None = None
    ) -> Sequence[SalesAction]:
        return self._store.list_actions(statuses)

    def find_action_by_idempotency_key(self, key: str) -> SalesAction | None:
        return self._store.find_action_by_idempotency_key(key)

    def save_actions(self, actions: Sequence[SalesAction]) -> None:
        self._store.save_actions(actions)

    def get_conversation(self, prospect_id: ProspectId) -> Conversation:
        return self._store.get_conversation(prospect_id)

    def append_messages(self, messages: Sequence[Message]) -> None:
        self._store.append_messages(messages)

    def append_audit_events(self, events: Sequence[AuditEvent]) -> None:
        """Record in the registry the opt-outs the Engine says came from a keyword, then the trail.

        The registry comes first: if it cannot be written the whole call fails before the trail is
        touched, and the Engine, which keeps unwritten events, offers them again.
        """
        for event in events:
            self._require_client(event.client_id)
        for event in events:
            if event.event_type == KEYWORD_OPT_OUT_EVENT and event.prospect_id is not None:
                record = self._find_raw(event.prospect_id)
                if record is not None:
                    self._consent.record_opt_out(
                        ContactInfo(phone=record.contact.phone, email=record.contact.email),
                        origin=Origin.INBOUND_KEYWORD,
                        reason=ReasonCode.KEYWORD,
                        actor=KEYWORD_ACTOR,
                    )
        self._store.append_audit_events(events)

    # ---- health --------------------------------------------------------------------------

    def problems(self) -> tuple[SourceProblem, ...]:
        return (*self._source.problems(), *self._store.problems(), *self._conflicts)

    def verify(self) -> list[str]:
        return [*self._source.verify(), *self._store.verify()]

    # ---- internals -----------------------------------------------------------------------

    def _require_client(self, client_id: ClientId) -> None:
        if client_id != self.client_id:
            raise ValueError("an object belongs to another client")

    def _public_id(self, native_id: str) -> str | None:
        try:
            return to_public_id(self._namespace, native_id)
        except InvalidNativeIdError:
            return None

    def _native_id(self, public_id: str) -> str | None:
        try:
            return to_native_id(self._namespace, public_id)
        except ValueError:
            return None

    def _gate(self, *, read_source: bool) -> _Gate:
        """Who is blocked now. Records the refusals of the source's latest reads first, so they
        take effect in this very read; the contacts go nowhere else."""
        snapshot = self._consent.snapshot()
        if not read_source:
            return _Gate(snapshot, frozenset())
        fresh = [c for c in self._source.refused_contacts() if not snapshot.blocks(c)]
        extra: set[str] = set()
        for contact in fresh:
            extra |= contact_keys(contact)
        if fresh:
            try:
                record_refusals(self._consent, fresh)
            except ConsentError:
                pass  # the registry is unhealthy, so it blocks everyone anyway; `extra` still holds
        return _Gate(snapshot, frozenset(extra))

    def _state_of(self, public_id: str) -> ProspectState | None:
        return self._store.get_states([ProspectId(public_id)]).get(ProspectId(public_id))

    def _merged(self, record: Prospect, public_id: str, gate: _Gate) -> Prospect:
        return merge_prospect(
            record,
            self._state_of(public_id),
            owned=self._source.capabilities.owned,
            public_id=public_id,
            contact_opted_out=gate.blocks(record.contact),
        )

    @staticmethod
    def _local_view(local: Prospect, gate: _Gate) -> Prospect:
        status = effective_status(None, local.status, contact_opted_out=gate.blocks(local.contact))
        return replace(local, status=status)

    def _find_raw(self, prospect_id: str) -> Prospect | None:
        """The prospect as its home holds it (source record, or local prospect), or None."""
        native = self._native_id(prospect_id)
        if native is not None:
            record = self._source.get_prospect(native)
            if record is not None:
                return record
        return self._store.get_local_prospect(ProspectId(prospect_id))

    def _validated(self, incoming: Prospect) -> _Change:
        """Which fields of this prospect go to the store; refuses what may not be written."""
        self._require_client(incoming.client_id)
        public = incoming.prospect_id
        native = self._native_id(public)
        record = self._source.get_prospect(native) if native is not None else None
        if record is not None:
            before = self._merged(record, public, self._gate(read_source=True))
            owned = self._source.capabilities.owned
        else:
            local = self._store.get_local_prospect(public)
            if local is None:
                raise KeyError(_UNKNOWN)
            before = self._local_view(local, self._gate(read_source=False))
            owned = frozenset(SOURCE_ONLY_FIELDS)  # a local prospect's identity never changes
        fields = changed_fields(before, incoming)
        # The status is the one field both sides speak about: Revansa's goes to the store and the
        # stricter of the two wins when they are read back.
        refused = {f for f in fields if f in owned and f is not ProspectField.STATUS}
        if refused:
            raise SourceReadOnlyError("a field owned by the source cannot be changed here")
        return _Change(ProspectId(public), incoming, fields)

    def _new_states(self, changes: Sequence[_Change]) -> list[ProspectState]:
        held = self._store.get_states([c.prospect_id for c in changes])
        states = []
        for change in changes:
            state = held.get(change.prospect_id) or ProspectState(
                client_id=self.client_id, prospect_id=change.prospect_id
            )
            states.append(_apply(state, change))
        return states


def _apply(state: ProspectState, change: _Change) -> ProspectState:
    new, fields = change.incoming, change.fields
    schedule: Mapping[str, Any] = {}
    if ProspectField.NEXT_ACTION in fields:
        schedule = {
            "next_action_type": new.schedule.next_action_type,
            "next_action_due_at": new.schedule.next_action_due_at,
        }
    for field, name in (
        (ProspectField.LAST_CONTACTED_AT, "last_contacted_at"),
        (ProspectField.LAST_INBOUND_AT, "last_inbound_at"),
        (ProspectField.FOLLOW_UP_COUNT, "follow_up_count"),
    ):
        if field in fields:
            schedule = {**schedule, name: getattr(new.schedule, name)}
    updated = state
    if schedule:
        updated = replace(updated, schedule=_scheduling(state.schedule, schedule))
    if ProspectField.STATUS in fields:
        updated = replace(updated, status=new.status)
    if ProspectField.STAGE in fields:
        updated = replace(updated, stage=new.stage)
    if ProspectField.CONVERSATION_ID in fields:
        updated = replace(updated, conversation_id=new.conversation_id)
    return updated


def _scheduling(current: Scheduling, values: Mapping[str, Any]) -> Scheduling:
    return replace(current, **values)


def _conflict(detail: str) -> SourceProblem:
    return SourceProblem(code=ProblemCode.DUPLICATE_CONTACT, detail=detail)
