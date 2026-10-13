"""In-memory stand-ins for the two ports, for tests. Deterministic: no clock, no randomness, no I/O.

`FakeProspectSource` plays a CRM or a spreadsheet; `FakeOperationalStore` plays the place Revansa
keeps its own data. Both are bound to one client and mirror the guarantees the ports state, so the
code that composes them can be tested without a provider, and a real adapter has a contract to meet
(see `tests/app/test_sourcing_contracts.py`).

They live with the tests, not in the shipped package: they are test support only, and a module of
the package that defines `append_facts` would have to be added to the architecture test that lists
who may record facts.

Failure injection works as in `FakeSalesRepository`: `fail_next(method, error)` makes the next calls
to a port method raise. Every call a source receives is noted in `requests` (`read:<method>` or
`write:<method>`), so a test can prove that a read-only source never sent a write.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sales_app.sourcing.contacts import contact_keys, lookup_keys
from sales_app.sourcing.errors import (
    SourceConflictError,
    SourceReadOnlyError,
    SourceUnavailableError,
    StateConflictError,
    StoreUnavailableError,
)
from sales_app.sourcing.fields import (
    OVERLAY_FIELDS,
    SOURCE_ONLY_FIELDS,
    ProspectField,
    field_value,
)
from sales_app.sourcing.identifiers import local_id_problem, native_id_problem
from sales_app.sourcing.ports import ProspectChange, ProspectState, SourceCapabilities
from sales_app.sourcing.problems import ProblemCode, SourceProblem
from sales_app.sourcing.statuses import StatusMapping, resolve_source_status
from sales_core.contracts import (
    ActionId,
    ActionStatus,
    AuditEvent,
    ClientId,
    ContactInfo,
    Conversation,
    ConversationId,
    Fact,
    Message,
    MessageId,
    Prospect,
    ProspectId,
    ProspectStatus,
    SalesAction,
    Scheduling,
    fact_identity,
)

_UNSET: Any = object()  # "no raw status given": the record's own `status` is used as it is

_PROBLEM_DETAIL = {
    ProblemCode.INVALID_NATIVE_ID: "a record has an id outside the accepted alphabet",
    ProblemCode.BLANK_STATUS: "a record has no status",
    ProblemCode.UNMAPPED_STATUS: "a record has a status that is not mapped",
    ProblemCode.UNREADABLE_RECORD: "a record could not be interpreted",
}


def default_capabilities(**overrides: Any) -> SourceCapabilities:
    """A CRM-like source: it owns who the people are and a status; Revansa's state is kept apart."""
    values: dict[str, Any] = {
        "owned": SOURCE_ONLY_FIELDS | {ProspectField.STATUS},
        "writable": frozenset({ProspectField.STATUS}),
        "id_namespace": "fake_crm",
        "consent_signal": True,
    }
    return SourceCapabilities(**(values | overrides))


def spreadsheet_capabilities(**overrides: Any) -> SourceCapabilities:
    """A spreadsheet-like source: it owns every field and keeps native ids as they are."""
    values: dict[str, Any] = {
        "owned": frozenset(ProspectField),
        "writable": frozenset(ProspectField),
        "can_create": True,
        "id_namespace": None,
        "atomic_compare_and_set": True,
        "consent_signal": True,
    }
    return SourceCapabilities(**(values | overrides))


@dataclass(slots=True)
class _Record:
    prospect: Prospect
    raw_status: Any = _UNSET
    do_not_contact: bool = False


class FakeProspectSource:
    """Implements `ProspectSource` for one client.

    Seed it with `add_record`. A record may carry a CRM's raw status text (`raw_status`, resolved
    through `status_mapping` as a real source would) and a do-not-contact flag. `add_unreadable`
    adds a record that cannot be interpreted at all. Writes are refused unless `allow_writes` is set
    (the default mirrors the real default: read-only).
    """

    def __init__(
        self,
        client_id: ClientId,
        *,
        capabilities: SourceCapabilities | None = None,
        status_mapping: StatusMapping | None = None,
        allow_writes: bool = False,
    ) -> None:
        self._client_id = client_id
        self._capabilities = capabilities if capabilities is not None else default_capabilities()
        self._mapping = status_mapping
        self._allow_writes = allow_writes
        self._records: dict[str, _Record] = {}
        self._unreadable = 0
        self._problems: tuple[SourceProblem, ...] = ()
        self._refused: tuple[ContactInfo, ...] = ()
        self._requests: list[str] = []
        self._failures: dict[str, list[Exception]] = {}
        self.verify_issues: list[str] = []

    # ---- test helpers (not part of the port) -------------------------------------------

    def add_record(
        self, prospect: Prospect, *, raw_status: Any = _UNSET, do_not_contact: bool = False
    ) -> None:
        if prospect.client_id != self._client_id:
            raise ValueError(f"a record belongs to client {prospect.client_id!r}")
        if prospect.prospect_id in self._records:
            raise ValueError("a record with this id already exists")
        if raw_status is not _UNSET and self._mapping is None:
            raise ValueError("a raw status needs a status_mapping")
        self._records[prospect.prospect_id] = _Record(prospect, raw_status, do_not_contact)

    def add_unreadable(self) -> None:
        self._unreadable += 1

    def clear_refusals(self) -> None:
        """A person in the source clears every do-not-contact flag."""
        for record in self._records.values():
            record.do_not_contact = False

    def human_edit(self, prospect: Prospect) -> None:
        """A person edits a record directly in the source, between a read and a write."""
        if prospect.prospect_id not in self._records:
            raise KeyError("unknown record")
        self._records[prospect.prospect_id].prospect = prospect

    def fail_next(self, method: str, error: Exception | None = None, *, times: int = 1) -> None:
        failure = error if error is not None else SourceUnavailableError("injected failure")
        self._failures.setdefault(method, []).extend([failure] * times)

    @property
    def requests(self) -> tuple[str, ...]:
        return tuple(self._requests)

    @property
    def records(self) -> dict[str, Prospect]:
        """What the source holds right now, by native id, exactly as stored (not as read)."""
        return {native_id: record.prospect for native_id, record in self._records.items()}

    # ---- ProspectSource port -------------------------------------------------------------

    @property
    def client_id(self) -> ClientId:
        return self._client_id

    @property
    def capabilities(self) -> SourceCapabilities:
        return self._capabilities

    def list_prospects(self) -> Sequence[Prospect]:
        self._request("read", "list_prospects")
        return [prospect for _, prospect in self._readable()]

    def get_prospect(self, native_id: str) -> Prospect | None:
        self._request("read", "get_prospect")
        return next((p for pid, p in self._readable() if pid == native_id), None)

    def find_prospect_by_contact(
        self, *, phone: str | None = None, email: str | None = None
    ) -> Prospect | None:
        self._request("read", "find_prospect_by_contact")
        wanted = lookup_keys(phone=phone, email=email)
        if not wanted:
            return None
        return next((p for _, p in self._readable() if contact_keys(p.contact) & wanted), None)

    def create_prospects(self, prospects: Sequence[Prospect]) -> None:
        self._refuse_unless_writable("create_prospects")
        if not self._capabilities.can_create:
            raise SourceReadOnlyError("this source cannot create prospects")
        self._request("write", "create_prospects")
        seen = set(self._records)
        for prospect in prospects:
            if prospect.client_id != self._client_id:
                raise ValueError(f"a prospect belongs to client {prospect.client_id!r}")
            if prospect.prospect_id in seen:
                raise ValueError("a prospect with this id already exists")
            seen.add(prospect.prospect_id)
        for prospect in prospects:
            self._records[prospect.prospect_id] = _Record(prospect)

    def update_prospects(self, changes: Sequence[ProspectChange]) -> None:
        self._refuse_unless_writable("update_prospects")
        self._request("write", "update_prospects")
        seen: set[str] = set()
        for change in changes:
            if change.prospect_id in seen:
                raise ValueError("a prospect appears twice in one call")
            seen.add(change.prospect_id)
            record = self._records.get(change.prospect_id)
            if record is None:
                raise KeyError("unknown prospect")
            if not set(change.new) <= self._capabilities.writable:
                raise SourceReadOnlyError("a field of this change cannot be written")
            stale = tuple(
                sorted(
                    field.value
                    for field, value in change.expected.items()
                    if field_value(record.prospect, field) != value
                )
            )
            if stale:
                raise SourceConflictError(change.prospect_id, stale)
        for change in changes:
            record = self._records[change.prospect_id]
            prospect = record.prospect
            for field, value in change.new.items():
                prospect = _with_field(prospect, field, value)
            record.prospect = prospect
            if ProspectField.STATUS in change.new:
                record.raw_status = _UNSET  # the status is now the one Revansa wrote

    def problems(self) -> tuple[SourceProblem, ...]:
        return self._problems

    def refused_contacts(self) -> tuple[ContactInfo, ...]:
        """No request: it reports what the latest reads saw (empty before any read)."""
        return self._refused

    def verify(self) -> list[str]:
        self._request("read", "verify")
        return list(self.verify_issues)

    # ---- internals -----------------------------------------------------------------------

    def _request(self, kind: str, method: str) -> None:
        queue = self._failures.get(method)
        if queue:
            raise queue.pop(0)
        self._requests.append(f"{kind}:{method}")

    def _refuse_unless_writable(self, method: str) -> None:
        queue = self._failures.get(method)
        if queue:
            raise queue.pop(0)
        if not self._allow_writes:  # refused before any request is made
            raise SourceReadOnlyError("this source is read-only")

    def _readable(self) -> list[tuple[str, Prospect]]:
        """The records that can be read, as the port returns them; sets `problems()` and
        `refused_contacts()`."""
        found: list[tuple[str, Prospect]] = []
        problems: list[SourceProblem] = []
        refused: dict[tuple[str, ...], ContactInfo] = {}
        namespaced = self._capabilities.id_namespace is not None

        def refuse(record: _Record) -> None:
            """A record left out that says "do not contact": keep its contact, and only that."""
            keys = contact_keys(record.prospect.contact)
            if record.do_not_contact and keys:
                contact = ContactInfo(
                    phone=record.prospect.contact.phone, email=record.prospect.contact.email
                )
                refused.setdefault(tuple(sorted(keys)), contact)

        for native_id, record in self._records.items():
            if namespaced and native_id_problem(native_id) is not None:
                problems.append(_problem(ProblemCode.INVALID_NATIVE_ID, None))
                refuse(record)
                continue
            prospect = record.prospect
            if record.raw_status is not _UNSET:
                assert self._mapping is not None
                resolution = resolve_source_status(
                    record.raw_status, self._mapping, do_not_contact=record.do_not_contact
                )
                if resolution.problem is not None:
                    problems.append(_problem(resolution.problem, native_id))
                    if resolution.opt_out_signal:
                        refuse(record)
                    continue
                prospect = replace(prospect, status=resolution.status)
            elif record.do_not_contact:
                prospect = replace(prospect, status=ProspectStatus.OPTED_OUT)
            found.append((native_id, self._neutral(prospect)))
        problems.extend(
            _problem(ProblemCode.UNREADABLE_RECORD, None) for _ in range(self._unreadable)
        )
        self._problems = tuple(problems)
        self._refused = tuple(refused.values())
        return found

    def _neutral(self, prospect: Prospect) -> Prospect:
        """Fields the source does not own come back empty, never as something it made up."""
        owned = self._capabilities.owned
        if OVERLAY_FIELDS <= owned:
            return prospect
        own = prospect.schedule
        next_owned = ProspectField.NEXT_ACTION in owned
        schedule = Scheduling(
            next_action_type=own.next_action_type if next_owned else None,
            next_action_due_at=own.next_action_due_at if next_owned else None,
            last_contacted_at=(
                own.last_contacted_at if ProspectField.LAST_CONTACTED_AT in owned else None
            ),
            last_inbound_at=own.last_inbound_at if ProspectField.LAST_INBOUND_AT in owned else None,
            follow_up_count=own.follow_up_count if ProspectField.FOLLOW_UP_COUNT in owned else 0,
        )
        return replace(
            prospect,
            status=prospect.status if ProspectField.STATUS in owned else ProspectStatus.NEW,
            stage=prospect.stage if ProspectField.STAGE in owned else None,
            conversation_id=(
                prospect.conversation_id if ProspectField.CONVERSATION_ID in owned else None
            ),
            schedule=schedule,
        )


def _problem(code: ProblemCode, native_id: str | None) -> SourceProblem:
    return SourceProblem(code=code, detail=_PROBLEM_DETAIL[code], native_id=native_id)


def _with_field(prospect: Prospect, field: ProspectField, value: Any) -> Prospect:
    schedule = prospect.schedule
    match field:
        case ProspectField.DISPLAY_NAME:
            return replace(prospect, display_name=value)
        case ProspectField.PHONE:
            return replace(prospect, contact=replace(prospect.contact, phone=value))
        case ProspectField.EMAIL:
            return replace(prospect, contact=replace(prospect.contact, email=value))
        case ProspectField.PREFERRED_CHANNEL:
            return replace(prospect, contact=replace(prospect.contact, preferred_channel=value))
        case ProspectField.LOCALE:
            return replace(prospect, locale=value)
        case ProspectField.STATUS:
            return replace(prospect, status=value)
        case ProspectField.STAGE:
            return replace(prospect, stage=value)
        case ProspectField.NEXT_ACTION:
            next_type, next_due = value
            return replace(
                prospect,
                schedule=replace(schedule, next_action_type=next_type, next_action_due_at=next_due),
            )
        case ProspectField.LAST_CONTACTED_AT:
            return replace(prospect, schedule=replace(schedule, last_contacted_at=value))
        case ProspectField.LAST_INBOUND_AT:
            return replace(prospect, schedule=replace(schedule, last_inbound_at=value))
        case ProspectField.FOLLOW_UP_COUNT:
            return replace(prospect, schedule=replace(schedule, follow_up_count=value))
        case ProspectField.CONVERSATION_ID:
            return replace(prospect, conversation_id=value)


def _recorded_at(fact: Fact) -> datetime:
    assert fact.recorded_at is not None  # `fact_identity` refuses facts without it
    return fact.recorded_at


class FakeOperationalStore:
    """Implements `OperationalStore` (so also `FactsReader` and `FactsWriter`) for one client.

    Two behaviours the `OperationalStore` port states explicitly, and this fake follows:

    - Facts are accepted for any prospect id, without claiming the prospect exists. The store cannot
      know the prospects a source holds: the code that composes them must check existence first.
    - A local prospect has a state from the moment it is created, at revision 0, taken from the
      prospect itself; later `save_states` calls move it forward. A refused creation leaves
      neither prospects nor states.
    """

    def __init__(self, client_id: ClientId) -> None:
        self._client_id = client_id
        self._actions: dict[ActionId, SalesAction] = {}
        self._messages: list[Message] = []
        self._message_ids: set[MessageId] = set()
        self._conversation_ids: dict[ProspectId, ConversationId] = {}
        self._audit: list[AuditEvent] = []
        self._facts: dict[str, tuple[ProspectId, Fact]] = {}
        self._states: dict[ProspectId, ProspectState] = {}
        self._local: dict[ProspectId, Prospect] = {}  # identity as created; state is in _states
        self._failures: dict[str, list[Exception]] = {}

    # ---- test helpers (not part of the port) -------------------------------------------

    def fail_next(self, method: str, error: Exception | None = None, *, times: int = 1) -> None:
        failure = error if error is not None else StoreUnavailableError("injected failure")
        self._failures.setdefault(method, []).extend([failure] * times)

    @property
    def messages(self) -> tuple[Message, ...]:
        return tuple(self._messages)

    @property
    def audit_events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._audit)

    # ---- the operational half of SalesRepository -------------------------------------------

    @property
    def client_id(self) -> ClientId:
        return self._client_id

    def list_actions(
        self, statuses: Collection[ActionStatus] | None = None
    ) -> Sequence[SalesAction]:
        self._maybe_fail("list_actions")
        return [a for a in self._actions.values() if statuses is None or a.status in statuses]

    def find_action_by_idempotency_key(self, key: str) -> SalesAction | None:
        self._maybe_fail("find_action_by_idempotency_key")
        return next((a for a in self._actions.values() if a.idempotency_key == key), None)

    def save_actions(self, actions: Sequence[SalesAction]) -> None:
        self._maybe_fail("save_actions")
        pending = dict(self._actions)
        for action in actions:
            self._require_client(action.client_id, f"action {action.action_id!r}")
            pending[action.action_id] = action
        owners: dict[str, ActionId] = {}
        for action in pending.values():
            if owners.setdefault(action.idempotency_key, action.action_id) != action.action_id:
                raise ValueError(f"idempotency key {action.idempotency_key!r} is already in use")
        self._actions = pending

    def get_conversation(self, prospect_id: ProspectId) -> Conversation:
        self._maybe_fail("get_conversation")
        return Conversation(
            conversation_id=self._conversation_id_for(prospect_id, self._conversation_ids),
            client_id=self._client_id,
            prospect_id=prospect_id,
            messages=tuple(m for m in self._messages if m.prospect_id == prospect_id),
        )

    def append_messages(self, messages: Sequence[Message]) -> None:
        self._maybe_fail("append_messages")
        seen = set(self._message_ids)
        assigned = dict(self._conversation_ids)
        for message in messages:
            self._require_client(message.client_id, f"message {message.message_id!r}")
            if message.message_id in seen:
                raise ValueError(f"message {message.message_id!r} already exists")
            seen.add(message.message_id)
            expected = self._conversation_id_for(message.prospect_id, assigned, message)
            if message.conversation_id != expected:
                raise ValueError(
                    f"message {message.message_id!r} is in conversation "
                    f"{message.conversation_id!r}, but prospect {message.prospect_id!r} "
                    f"uses {expected!r}"
                )
            assigned[message.prospect_id] = expected
        self._messages.extend(messages)
        self._message_ids = seen
        self._conversation_ids = assigned

    def append_audit_events(self, events: Sequence[AuditEvent]) -> None:
        self._maybe_fail("append_audit_events")
        for event in events:
            self._require_client(event.client_id, f"audit event {event.event_type!r}")
        self._audit.extend(events)

    # ---- FactsReader / FactsWriter ---------------------------------------------------------

    def get_facts(self, prospect_id: ProspectId) -> Sequence[Fact]:
        self._maybe_fail("get_facts")
        own = [f for pid, f in self._facts.values() if pid == prospect_id]
        return sorted(own, key=_recorded_at)  # stable: ties keep append order

    def append_facts(self, prospect_id: ProspectId, facts: Sequence[Fact]) -> None:
        self._maybe_fail("append_facts")
        identified = [(fact_identity(self._client_id, prospect_id, f), f) for f in facts]
        for fact_id, fact in identified:
            self._facts.setdefault(fact_id, (prospect_id, fact))

    # ---- Revansa's state for each prospect ---------------------------------------------------

    def get_states(self, prospect_ids: Collection[ProspectId]) -> dict[ProspectId, ProspectState]:
        self._maybe_fail("get_states")
        return {pid: self._states[pid] for pid in prospect_ids if pid in self._states}

    def list_states(self) -> dict[ProspectId, ProspectState]:
        self._maybe_fail("list_states")
        return dict(self._states)

    def save_states(self, states: Sequence[ProspectState]) -> None:
        self._maybe_fail("save_states")
        seen: set[ProspectId] = set()
        for state in states:
            self._require_client(state.client_id, f"state of {state.prospect_id!r}")
            if state.prospect_id in seen:
                raise ValueError("a prospect appears twice in one call")
            seen.add(state.prospect_id)
            stored = self._states.get(state.prospect_id)
            if state.revision != (stored.revision if stored is not None else 0):
                raise StateConflictError(state.prospect_id)
        for state in states:
            self._states[state.prospect_id] = replace(state, revision=state.revision + 1)

    # ---- prospects created locally -----------------------------------------------------------

    def list_local_prospects(self) -> list[Prospect]:
        self._maybe_fail("list_local_prospects")
        return [self._local_view(pid) for pid in self._local]

    def get_local_prospect(self, prospect_id: ProspectId) -> Prospect | None:
        self._maybe_fail("get_local_prospect")
        return self._local_view(prospect_id) if prospect_id in self._local else None

    def find_local_by_contact(
        self,
        *,
        phone: str | None = None,
        email: str | None = None,
        telegram_chat_id: str | None = None,
    ) -> Prospect | None:
        self._maybe_fail("find_local_by_contact")
        wanted = lookup_keys(phone=phone, email=email, telegram_chat_id=telegram_chat_id)
        if not wanted:
            return None
        return next(
            (
                self._local_view(pid)
                for pid, p in self._local.items()
                if contact_keys(p.contact) & wanted
            ),
            None,
        )

    def create_local_prospects(self, prospects: Sequence[Prospect]) -> None:
        self._maybe_fail("create_local_prospects")
        ids = set(self._local)
        taken: set[str] = set()
        for existing in self._local.values():
            taken |= contact_keys(existing.contact)
        for prospect in prospects:
            self._require_client(prospect.client_id, f"prospect {prospect.prospect_id!r}")
            if local_id_problem(prospect.prospect_id) is not None:
                raise ValueError("the id cannot be held safely by the store")
            keys = contact_keys(prospect.contact)
            if prospect.prospect_id in ids:
                raise ValueError("a local prospect with this id already exists")
            if keys & taken:
                raise ValueError("the contact is already in use by another local prospect")
            ids.add(prospect.prospect_id)
            taken |= keys
        for prospect in prospects:
            self._local[prospect.prospect_id] = prospect
            self._states[prospect.prospect_id] = ProspectState(
                client_id=prospect.client_id,
                prospect_id=prospect.prospect_id,
                status=prospect.status,
                stage=prospect.stage,
                schedule=prospect.schedule,
                conversation_id=prospect.conversation_id,
            )

    def problems(self) -> tuple[SourceProblem, ...]:
        return ()

    def verify(self) -> list[str]:
        self._maybe_fail("verify")
        return []

    # ---- internals -----------------------------------------------------------------------

    def _local_view(self, prospect_id: ProspectId) -> Prospect:
        identity, state = self._local[prospect_id], self._states[prospect_id]
        return replace(
            identity,
            contact=replace(
                identity.contact,
                telegram_chat_id=state.telegram_chat_id or identity.contact.telegram_chat_id,
            ),
            status=state.status if state.status is not None else ProspectStatus.NEW,
            stage=state.stage,
            schedule=state.schedule,
            conversation_id=state.conversation_id,
        )

    def _require_client(self, client_id: ClientId, what: str) -> None:
        if client_id != self._client_id:
            raise ValueError(f"{what} belongs to client {client_id!r}, not {self._client_id!r}")

    def _maybe_fail(self, method: str) -> None:
        queue = self._failures.get(method)
        if queue:
            raise queue.pop(0)

    def _conversation_id_for(
        self,
        prospect_id: ProspectId,
        assigned: dict[ProspectId, ConversationId],
        first_message: Message | None = None,
    ) -> ConversationId:
        """History id already in use, else the prospect's own reference, else a default."""
        if prospect_id in assigned:
            return assigned[prospect_id]
        state = self._states.get(prospect_id)
        if state is not None and state.conversation_id is not None:
            return state.conversation_id
        if first_message is not None:
            return first_message.conversation_id
        return ConversationId(f"conv-{prospect_id}")
