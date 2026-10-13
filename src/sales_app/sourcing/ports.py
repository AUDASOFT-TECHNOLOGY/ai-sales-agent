"""The two ports a client's data is split across, owned by the application.

`ProspectSource` is where the people are: a spreadsheet, a CRM. `OperationalStore` is where
Revansa keeps what it does: actions, messages, facts, the audit trail, and its own state for each
prospect. Today one object (`sales_core.ports.SalesRepository`, in practice the Google Sheets
adapter) plays both roles. These ports let a client combine a source and a store that are not the
same provider; a composing repository (a later increment) presents the pair to the Core as the one
`SalesRepository` it already knows, so the Core, the Engine and the Brains never change.

The ports live here, not in the Core, because their only consumer is the application: the Core asks
for a repository and does not care how it is assembled. Adapters therefore do not import this
module; each provider's descriptor wraps its adapter into these shapes.

Every object is bound to one client, exactly like `SalesRepository`: an object of another client is
refused with `ValueError`. A source speaks **native** ids; the composing code turns them into the
ids the Core sees (see `identifiers`). Not covered here, on purpose: the opt-out registry and the
write-back ledger, which are separate ports added with their own increments.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from sales_app.sourcing.fields import SOURCE_ONLY_FIELDS, ProspectField
from sales_app.sourcing.identifiers import check_namespace
from sales_app.sourcing.problems import SourceProblem
from sales_core.contracts import (
    ActionStatus,
    AuditEvent,
    ClientId,
    ContactInfo,
    Conversation,
    ConversationId,
    Message,
    Prospect,
    ProspectId,
    ProspectStatus,
    SalesAction,
    Scheduling,
)
from sales_core.ports import FactsReader, FactsWriter


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceCapabilities:
    """What a source is and may do. Declared by its provider, never discovered at run time.

    - `owned`: the fields whose truth lives in the source and which it returns reliably. It always
      owns what a person is (`SOURCE_ONLY_FIELDS`); a spreadsheet that also holds Revansa's own
      state lists those fields too, and then nothing is kept apart for them.
    - `writable`: what its interface can technically accept (a subset of `owned`). Whether a field
      is actually written is the operator's decision (write-back is off by default).
    - `can_create`: whether it can create a prospect. Unknown senders are created in the store
      either way; a source is never asked to.
    - `id_namespace`: the prefix of its public ids (`<namespace>:<native id>`), or None to leave
      native ids as they are (Google Sheets).
    - `atomic_compare_and_set`: whether it can refuse a write if the value changed since it was
      read. Without it a short window remains between the last read and the write.
    - `consent_signal`: whether it can express "do not contact" and maps it to `OPTED_OUT`.
    """

    owned: frozenset[ProspectField]
    writable: frozenset[ProspectField] = frozenset()
    can_create: bool = False
    id_namespace: str | None = None
    atomic_compare_and_set: bool = False
    consent_signal: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "owned", frozenset(self.owned))
        object.__setattr__(self, "writable", frozenset(self.writable))
        check_namespace(self.id_namespace)
        if not SOURCE_ONLY_FIELDS <= self.owned:
            raise ValueError("a source always owns name, contact, channel and language")
        if not self.writable <= self.owned:
            raise ValueError("a source can only write fields it owns")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProspectChange:
    """One prospect's change to write to a source.

    `new` holds the values to write and `expected` the values they replace, as read earlier. The
    source writes only if every expected value is still current, otherwise it raises
    `SourceConflictError` and writes nothing, so a person's edit made in between is never lost.
    The two cover exactly the same fields.
    """

    prospect_id: str  # the source's native id
    new: Mapping[ProspectField, Any]
    expected: Mapping[ProspectField, Any]

    def __post_init__(self) -> None:
        if not self.prospect_id:
            raise ValueError("a change needs a prospect id")
        if not self.new:
            raise ValueError("a change needs at least one field")
        if set(self.new) != set(self.expected):
            raise ValueError("a change must give the expected value of each field it writes")
        object.__setattr__(self, "new", MappingProxyType(dict(self.new)))
        object.__setattr__(self, "expected", MappingProxyType(dict(self.expected)))


@dataclass(frozen=True, slots=True, kw_only=True)
class ProspectState:
    """What Revansa itself holds about one prospect. Never a name, a number or an address, with one
    exception: `telegram_chat_id`, the chat the person opened with the client's bot. No source can
    hold it (the person gave it to the bot, not to the CRM), so it lives here or nowhere.

    `status` is None until Revansa first sets one. `revision` counts the writes of this record: a
    save that carries a stale revision is refused (`StateConflictError`), so a concurrent edit by a
    person or another process is detected rather than overwritten. A fresh state has revision 0.
    """

    client_id: ClientId
    prospect_id: ProspectId  # the id the Core sees (public)
    status: ProspectStatus | None = None
    stage: str | None = None
    schedule: Scheduling = field(default_factory=Scheduling)
    conversation_id: ConversationId | None = None
    telegram_chat_id: str | None = None
    lane_id: str | None = None  # the lane the person was attributed to; never changed once set
    status_reason: str | None = None
    status_changed_at: datetime | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        if not self.client_id or not self.prospect_id:
            raise ValueError("a state needs a client id and a prospect id")
        if self.revision < 0:
            raise ValueError("revision must be >= 0")
        if self.status_changed_at is not None and self.status_changed_at.tzinfo is None:
            raise ValueError("status_changed_at must be timezone-aware")


@runtime_checkable
class ProspectSource(Protocol):
    """Read access to a client's prospects, and the narrow, permitted writes to them.

    Read-only unless the operator allowed otherwise: a source built read-only raises
    `SourceReadOnlyError` from `create_prospects` and `update_prospects` **without sending any
    request**. It never invents a status, an id or a contact.
    """

    @property
    def client_id(self) -> ClientId: ...

    @property
    def capabilities(self) -> SourceCapabilities: ...

    def list_prospects(self) -> Sequence[Prospect]:
        """Every readable prospect, or an error: never a partial list. A record that cannot be
        interpreted (unreadable, a blank or unmapped status, an id outside the accepted alphabet)
        is left out and described by `problems()`. Ids are native; fields the source does not own
        carry neutral values and are ignored by the caller."""
        ...

    def get_prospect(self, native_id: str) -> Prospect | None:
        """The prospect with this native id, or None if there is none (or it was left out)."""
        ...

    def find_prospect_by_contact(
        self, *, phone: str | None = None, email: str | None = None
    ) -> Prospect | None:
        """Compared as `sales_app.sourcing.contacts` does. None if there is no match."""
        ...

    def create_prospects(self, prospects: Sequence[Prospect]) -> None:
        """Raises `SourceReadOnlyError` unless the source can create and was allowed to."""
        ...

    def update_prospects(self, changes: Sequence[ProspectChange]) -> None:
        """Write only the fields named, each only if still as expected. All or nothing per call.
        Raises `SourceReadOnlyError`, `SourceConflictError` or `KeyError` for an unknown id."""
        ...

    def problems(self) -> tuple[SourceProblem, ...]:
        """The records left out by the latest reads, and why."""
        ...

    def refused_contacts(self) -> tuple[ContactInfo, ...]:
        """The contacts of records left out by the latest reads that carry a do-not-contact signal.

        A record that is left out (blank or unmapped status, an id outside the accepted alphabet)
        never reaches `list_prospects`, but a refusal it carries must not be lost: the code that
        records opt-outs reads it here. A record that is kept with that signal is not repeated
        here; it is listed as `OPTED_OUT`.

        Only a phone number and an e-mail address: never a name, never the preferred channel or
        any other field, and a record with no usable contact is absent (it cannot be contacted
        anyway). Duplicates are collapsed, in a stable order. It describes what the latest reads
        saw; it makes no request of its own. **The caller records these contacts and nothing
        else: they are never logged, displayed or copied into `problems()`.**
        """
        ...

    def verify(self) -> list[str]:
        """Check the connection and the layout without writing. Empty means ready; 'warning:' is
        soft."""
        ...


@runtime_checkable
class OperationalStore(FactsReader, FactsWriter, Protocol):
    """Where Revansa keeps what it does, for one client.

    The operational half of `SalesRepository`, unchanged in meaning (an action's idempotency key is
    unique, messages and audit are append-only, facts are idempotent by `fact_identity`; those come
    from `FactsReader` and `FactsWriter`), plus Revansa's state for each prospect and the prospects
    it creates itself from unknown senders.

    A **local prospect** is one the Engine created from an unknown sender. It lives only here, as
    one self-contained record (identity and state together), and is never copied to a source. Its
    identity does not change after it is created; its state does.

    **Facts and prospects the store does not know.** The facts ports say a write for an unknown
    prospect raises `KeyError`. This store cannot honour that, because it does not know the
    prospects a source holds: `append_facts` therefore accepts any prospect id, records the facts and
    never claims to have checked that the prospect exists. **The code that composes a source and a
    store must check that the prospect exists (in the source or among the local prospects) before
    it writes facts, and raise `KeyError` itself.** The store is not a safeguard against facts
    recorded for an id that exists nowhere.
    """

    @property
    def client_id(self) -> ClientId: ...

    # ---- the operational half of SalesRepository -----------------------------------------

    def list_actions(
        self, statuses: Collection[ActionStatus] | None = None
    ) -> Sequence[SalesAction]: ...

    def find_action_by_idempotency_key(self, key: str) -> SalesAction | None: ...

    def save_actions(self, actions: Sequence[SalesAction]) -> None: ...

    def get_conversation(self, prospect_id: ProspectId) -> Conversation: ...

    def append_messages(self, messages: Sequence[Message]) -> None: ...

    def append_audit_events(self, events: Sequence[AuditEvent]) -> None: ...

    # ---- Revansa's state for each prospect ---------------------------------------------------

    def get_states(
        self, prospect_ids: Collection[ProspectId]
    ) -> Mapping[ProspectId, ProspectState]:
        """The state of each of these prospects that has one (a prospect with none is absent)."""
        ...

    def list_states(self) -> Mapping[ProspectId, ProspectState]: ...

    def save_states(self, states: Sequence[ProspectState]) -> None:
        """Insert or update. Each state carries the `revision` it was read at (0 for a new one);
        the store keeps it only if that is still the stored revision, and stores `revision + 1`.
        Raises `StateConflictError`, writing nothing, if any is stale. Accepts the state of a
        source's prospect and of a local one."""
        ...

    # ---- prospects created locally -----------------------------------------------------------

    def list_local_prospects(self) -> Sequence[Prospect]: ...

    def get_local_prospect(self, prospect_id: ProspectId) -> Prospect | None: ...

    def find_local_by_contact(
        self,
        *,
        phone: str | None = None,
        email: str | None = None,
        telegram_chat_id: str | None = None,
    ) -> Prospect | None: ...

    def create_local_prospects(self, prospects: Sequence[Prospect]) -> None:
        """Create local prospects, all or none.

        Raises `ValueError`, writing nothing, for an id or a contact already in use, an id the
        store cannot hold safely, or a prospect of another client.

        **Initial state.** Creating a local prospect also creates its state, **at revision 0**,
        taken from the prospect itself (status, stage, schedule, conversation id), so that
        `get_states` returns it at once. The first `save_states` for it carries revision 0 and
        stores revision 1. A refused call creates neither prospects nor states.
        """
        ...

    def problems(self) -> tuple[SourceProblem, ...]: ...

    def verify(self) -> list[str]: ...
