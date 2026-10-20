"""The `OperationalStore` of one client, kept in a SQL database.

This is the persistent counterpart of the in-memory fake the contract tests use, and it meets the
same contract: an action's idempotency key is unique, messages and audit are append-only, facts are
idempotent by `fact_identity`, a state is saved only from the revision it was read at, and a local
prospect is created with its state at revision 0. Every object is bound to one client: an object of
another client is refused with `ValueError` before anything is written.

The SQL itself lives in `sales_app.persistence.operational_rows` (which knows no Core type); this
module turns the Core's objects into documents and back (`store_codec`) and enforces what the port
states. Like the port, it **cannot know which prospects a source holds**: `append_facts` accepts
any prospect id, and the code that composes a source and a store checks that the prospect exists.

Nothing here logs, or puts an id, a name, a number or an address in an error message.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

from sales_app.read_model.search import search_text
from sales_app.sourcing import store_codec as codec
from sales_app.sourcing.contacts import contact_keys, lookup_keys
from sales_app.sourcing.errors import StoreError
from sales_app.sourcing.identifiers import local_id_problem
from sales_app.sourcing.ports import ProspectState
from sales_app.sourcing.problems import SourceProblem
from sales_core.contracts import (
    ActionId,
    ActionStatus,
    AuditEvent,
    ClientId,
    Conversation,
    ConversationId,
    Fact,
    Message,
    Prospect,
    ProspectId,
    ProspectStatus,
    SalesAction,
    fact_identity,
)

Row = Mapping[str, Any]


class OperationalRows(Protocol):
    """The rows layer this store talks to (`SqlOperationalRows`): primitives and documents only."""

    @property
    def client_id(self) -> str: ...
    def verify(self) -> list[str]: ...
    def list_actions(self, statuses: Collection[str] | None = None) -> list[Any]: ...
    def find_action_by_key(self, key: str) -> Any | None: ...
    def save_actions(self, rows: Sequence[Row]) -> None: ...
    def conversation_id_of(self, prospect_id: str) -> str | None: ...
    def list_messages(self, prospect_id: str) -> list[Any]: ...
    def append_messages(self, rows: Sequence[Row]) -> None: ...
    def append_audit(self, rows: Sequence[Row]) -> None: ...
    def list_facts(self, prospect_id: str) -> list[Any]: ...
    def append_facts(self, prospect_id: str, rows: Sequence[Row]) -> None: ...
    def get_states(self, prospect_ids: Collection[str]) -> dict[str, Row]: ...
    def list_states(self) -> dict[str, Row]: ...
    def save_states(self, rows: Sequence[Row]) -> None: ...
    def list_local(self) -> list[Row]: ...
    def get_local(self, prospect_id: str) -> Row | None: ...
    def find_local_by_keys(self, keys: Collection[str]) -> Row | None: ...
    def create_local(self, rows: Sequence[Row]) -> None: ...
    def index_prospects(self, rows: Sequence[Row]) -> int: ...


_UNREADABLE = "a stored record could not be read"


class SqlOperationalStore:
    """Implements `OperationalStore` (so also `FactsReader` and `FactsWriter`) for one client."""

    def __init__(self, rows: OperationalRows) -> None:
        self._rows = rows
        self._client_id = ClientId(rows.client_id)

    def __repr__(self) -> str:
        return f"SqlOperationalStore(client={self._client_id!r})"

    @property
    def client_id(self) -> ClientId:
        return self._client_id

    # ---- actions, messages, audit ----------------------------------------------------------

    def list_actions(
        self, statuses: Collection[ActionStatus] | None = None
    ) -> Sequence[SalesAction]:
        wanted = None if statuses is None else {str(s) for s in statuses}
        return [self._read(codec.action_from_doc, d) for d in self._rows.list_actions(wanted)]

    def find_action_by_idempotency_key(self, key: str) -> SalesAction | None:
        doc = self._rows.find_action_by_key(key)
        return None if doc is None else self._read(codec.action_from_doc, doc)

    def save_actions(self, actions: Sequence[SalesAction]) -> None:
        for action in actions:
            self._require_client(action.client_id)
        keys: dict[str, ActionId] = {}
        for action in actions:  # two actions of one call must not share a key either
            if keys.setdefault(action.idempotency_key, action.action_id) != action.action_id:
                raise ValueError("an idempotency key is already in use")
        self._rows.save_actions(
            [
                {
                    "action_id": a.action_id,
                    "idempotency_key": a.idempotency_key,
                    "status": str(a.status),
                    "document": codec.action_to_doc(a),
                    "prospect_id": a.prospect_id,
                    "action_type": str(a.action_type),
                    "due_at": a.due_at,
                }
                for a in actions
            ]
        )

    def get_conversation(self, prospect_id: ProspectId) -> Conversation:
        known = self._rows.conversation_id_of(prospect_id)
        messages = tuple(
            self._read(codec.message_from_doc, d) for d in self._rows.list_messages(prospect_id)
        )
        return Conversation(
            conversation_id=ConversationId(known or f"conv-{prospect_id}"),
            client_id=self._client_id,
            prospect_id=prospect_id,
            messages=messages,
        )

    def append_messages(self, messages: Sequence[Message]) -> None:
        for message in messages:
            self._require_client(message.client_id)
        self._rows.append_messages(
            [
                {
                    "message_id": m.message_id,
                    "prospect_id": m.prospect_id,
                    "conversation_id": m.conversation_id,
                    "document": codec.message_to_doc(m),
                    "direction": str(m.direction),
                    "channel": str(m.channel),
                    "sent_at": m.timestamp,
                }
                for m in messages
            ]
        )

    def append_audit_events(self, events: Sequence[AuditEvent]) -> None:
        for event in events:
            self._require_client(event.client_id)
        self._rows.append_audit(
            [
                {
                    "occurred_at": e.timestamp,
                    "event_type": e.event_type,
                    "prospect_id": e.prospect_id,
                    "action_id": e.action_id,
                    "document": codec.audit_to_doc(e),
                }
                for e in events
            ]
        )

    # ---- FactsReader / FactsWriter ---------------------------------------------------------

    def get_facts(self, prospect_id: ProspectId) -> Sequence[Fact]:
        return [self._read(codec.fact_from_doc, d) for d in self._rows.list_facts(prospect_id)]

    def append_facts(self, prospect_id: ProspectId, facts: Sequence[Fact]) -> None:
        rows = []
        for fact in facts:
            rows.append(
                {
                    "fact_id": fact_identity(self._client_id, prospect_id, fact),
                    "recorded_at": fact.recorded_at,
                    "document": codec.fact_to_doc(fact),
                    "fact_key": fact.key,
                }
            )
        self._rows.append_facts(prospect_id, rows)

    # ---- Revansa's state for each prospect ---------------------------------------------------

    def get_states(self, prospect_ids: Collection[ProspectId]) -> dict[ProspectId, ProspectState]:
        return self._states(self._rows.get_states(set(prospect_ids)))

    def list_states(self) -> dict[ProspectId, ProspectState]:
        return self._states(self._rows.list_states())

    def save_states(self, states: Sequence[ProspectState]) -> None:
        seen: set[ProspectId] = set()
        for state in states:
            self._require_client(state.client_id)
            if state.prospect_id in seen:
                raise ValueError("a prospect appears twice in one call")
            seen.add(state.prospect_id)
        self._rows.save_states(
            [
                {
                    "prospect_id": s.prospect_id,
                    "revision": s.revision,
                    "conversation_id": s.conversation_id,
                    "document": codec.state_to_doc(s),
                }
                for s in states
            ]
        )

    # ---- prospects created locally -----------------------------------------------------------

    def list_local_prospects(self) -> list[Prospect]:
        return [self._local(row) for row in self._rows.list_local()]

    def get_local_prospect(self, prospect_id: ProspectId) -> Prospect | None:
        row = self._rows.get_local(prospect_id)
        return None if row is None else self._local(row)

    def find_local_by_contact(
        self,
        *,
        phone: str | None = None,
        email: str | None = None,
        telegram_chat_id: str | None = None,
    ) -> Prospect | None:
        wanted = lookup_keys(phone=phone, email=email, telegram_chat_id=telegram_chat_id)
        if not wanted:
            return None
        row = self._rows.find_local_by_keys(sorted(wanted))
        return None if row is None else self._local(row)

    def create_local_prospects(self, prospects: Sequence[Prospect]) -> None:
        for prospect in prospects:
            self._require_client(prospect.client_id)
            if local_id_problem(prospect.prospect_id) is not None:
                raise ValueError("the id cannot be held safely by the store")
        self._rows.create_local(
            [
                {
                    "prospect_id": p.prospect_id,
                    "contact_keys": sorted(contact_keys(p.contact)),
                    "document": codec.prospect_to_doc(p),
                    "conversation_id": p.conversation_id,
                    "state_document": codec.state_to_doc(
                        ProspectState(
                            client_id=p.client_id,
                            prospect_id=p.prospect_id,
                            status=p.status,
                            stage=p.stage,
                            schedule=p.schedule,
                            conversation_id=p.conversation_id,
                        )
                    ),
                }
                for p in prospects
            ]
        )

    def index_prospects(
        self, prospects: Sequence[Prospect], origins: Mapping[str, tuple[str, str]] | None = None
    ) -> int:
        """Refresh the read model of these prospects (see `op_prospect_index`): what a list filters,
        searches and sorts by. `origins` maps a prospect id to (source id, id in that source).
        Returns the number of rows written. A projection, never the truth."""
        rows = []
        for p in prospects:
            self._require_client(p.client_id)
            origin = (origins or {}).get(p.prospect_id)
            moments = [
                m for m in (p.schedule.last_contacted_at, p.schedule.last_inbound_at) if m
            ]
            rows.append(
                {
                    "prospect_id": p.prospect_id,
                    **({} if origin is None else {"source_id": origin[0], "external_id": origin[1]}),
                    "lane_id": p.lane_id,
                    "display_name": p.display_name,
                    "email": p.contact.email,
                    "phone": p.contact.phone,
                    "search_text": search_text(p.display_name, p.contact.email, p.contact.phone),
                    "status": str(p.status),
                    "stage": p.stage,
                    "locale": p.locale,
                    "channel": None
                    if p.contact.preferred_channel is None
                    else str(p.contact.preferred_channel),
                    "next_action_type": None
                    if p.schedule.next_action_type is None
                    else str(p.schedule.next_action_type),
                    "next_action_at": p.schedule.next_action_due_at,
                    "last_contacted_at": p.schedule.last_contacted_at,
                    "last_inbound_at": p.schedule.last_inbound_at,
                    "last_activity_at": max(moments) if moments else None,
                    "follow_up_count": p.schedule.follow_up_count,
                    "needs_human": p.status is ProspectStatus.PAUSED,
                    "conversation_id": p.conversation_id,
                }
            )
        return self._rows.index_prospects(rows)

    def problems(self) -> tuple[SourceProblem, ...]:
        return ()

    def verify(self) -> list[str]:
        return self._rows.verify()

    # ---- internals -----------------------------------------------------------------------

    def _require_client(self, client_id: ClientId) -> None:
        if client_id != self._client_id:
            raise ValueError("the object belongs to another client")

    @staticmethod
    def _read[T](build, document: Any) -> T:
        try:
            return build(document)
        except (KeyError, TypeError, ValueError):
            raise StoreError(_UNREADABLE) from None

    def _states(self, rows: Mapping[str, Row]) -> dict[ProspectId, ProspectState]:
        return {ProspectId(pid): self._state(row) for pid, row in rows.items()}

    def _state(self, row: Row) -> ProspectState:
        try:
            return codec.state_from_doc(
                self._client_id, row["prospect_id"], row["revision"], row["document"]
            )
        except (KeyError, TypeError, ValueError):
            raise StoreError(_UNREADABLE) from None

    def _local(self, row: Row) -> Prospect:
        """The identity as created, with the state it has now (a local prospect always has one)."""
        identity = self._read(codec.prospect_from_doc, row["document"])
        if row["state"] is None:
            raise StoreError(_UNREADABLE)
        state = self._state(row["state"])
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
            lane_id=state.lane_id or identity.lane_id,
        )
