"""In-memory sales repository for tests. Stands in for the Google Sheets adapter."""

from collections.abc import Collection, Iterable, Sequence

from sales_core.contracts import (
    ActionId,
    ActionStatus,
    AuditEvent,
    ClientId,
    Conversation,
    ConversationId,
    Message,
    MessageId,
    Prospect,
    ProspectId,
    SalesAction,
)

# Port methods that support failure injection (see `fail_next`).
FAILABLE_METHODS = frozenset(
    {
        "list_prospects",
        "get_prospect",
        "find_prospect_by_contact",
        "update_prospects",
        "list_actions",
        "find_action_by_idempotency_key",
        "save_actions",
        "get_conversation",
        "append_messages",
        "append_audit_events",
    }
)


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


class FakeSalesRepository:
    """Implements `sales_core.ports.SalesRepository` for one client.

    Mirrors the guarantees a real adapter must give: objects of another client are rejected,
    writes are all-or-nothing per batch, action idempotency keys are unique, and history is
    append-only with a stable conversation id per prospect. Seed data with the constructor or
    `add_prospects`; inspect what the code under test wrote via `messages` and `audit_events`
    (or the port's own read methods).
    """

    def __init__(
        self,
        client_id: ClientId,
        *,
        prospects: Iterable[Prospect] = (),
        actions: Sequence[SalesAction] = (),
        messages: Sequence[Message] = (),
    ) -> None:
        self._client_id = client_id
        self._prospects: dict[ProspectId, Prospect] = {}
        self._actions: dict[ActionId, SalesAction] = {}
        self._messages: list[Message] = []
        self._message_ids: set[MessageId] = set()
        self._conversation_ids: dict[ProspectId, ConversationId] = {}
        self._audit: list[AuditEvent] = []
        self._failures: dict[str, list[Exception]] = {}
        self.add_prospects(*prospects)
        self.save_actions(actions)
        self.append_messages(messages)

    # ---- test helpers (not part of the port) -------------------------------------------

    def add_prospects(self, *prospects: Prospect) -> None:
        """Seed new prospects. Unlike `update_prospects`, they must not exist yet."""
        seen = set(self._prospects)
        for prospect in prospects:
            self._require_client(prospect.client_id, f"prospect {prospect.prospect_id!r}")
            if prospect.prospect_id in seen:
                raise ValueError(f"prospect {prospect.prospect_id!r} already exists")
            seen.add(prospect.prospect_id)
        for prospect in prospects:
            self._prospects[prospect.prospect_id] = prospect

    def fail_next(self, method: str, error: Exception | None = None, *, times: int = 1) -> None:
        """Make the next `times` calls to a port method raise, to test error handling."""
        if method not in FAILABLE_METHODS:
            raise ValueError(f"unknown repository method {method!r}")
        failure = error if error is not None else RuntimeError(f"injected failure in {method}")
        self._failures.setdefault(method, []).extend([failure] * times)

    @property
    def messages(self) -> tuple[Message, ...]:
        """All stored messages, in append order, across prospects."""
        return tuple(self._messages)

    @property
    def audit_events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._audit)

    # ---- SalesRepository port ------------------------------------------------------------

    @property
    def client_id(self) -> ClientId:
        return self._client_id

    def list_prospects(self) -> Sequence[Prospect]:
        self._maybe_fail("list_prospects")
        return list(self._prospects.values())

    def get_prospect(self, prospect_id: ProspectId) -> Prospect | None:
        self._maybe_fail("get_prospect")
        return self._prospects.get(prospect_id)

    def find_prospect_by_contact(
        self, *, phone: str | None = None, email: str | None = None
    ) -> Prospect | None:
        self._maybe_fail("find_prospect_by_contact")
        wanted_phone = _digits(phone) if phone else ""
        wanted_email = email.strip().casefold() if email else ""
        for prospect in self._prospects.values():
            contact = prospect.contact
            if wanted_phone and contact.phone and _digits(contact.phone) == wanted_phone:
                return prospect
            if wanted_email and contact.email and contact.email.strip().casefold() == wanted_email:
                return prospect
        return None

    def update_prospects(self, prospects: Sequence[Prospect]) -> None:
        self._maybe_fail("update_prospects")
        for prospect in prospects:
            self._require_client(prospect.client_id, f"prospect {prospect.prospect_id!r}")
            if prospect.prospect_id not in self._prospects:
                raise KeyError(f"unknown prospect {prospect.prospect_id!r}")
        for prospect in prospects:
            self._prospects[prospect.prospect_id] = prospect

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

    # ---- internals -----------------------------------------------------------------------

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
        prospect = self._prospects.get(prospect_id)
        if prospect is not None and prospect.conversation_id is not None:
            return prospect.conversation_id
        if first_message is not None:
            return first_message.conversation_id
        return ConversationId(f"conv-{prospect_id}")
