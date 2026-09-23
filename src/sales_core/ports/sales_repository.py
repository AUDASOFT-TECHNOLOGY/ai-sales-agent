"""Port for the operational data store (Google Sheets in the MVP)."""

from collections.abc import Collection, Sequence
from typing import Protocol, runtime_checkable

from sales_core.contracts import (
    ActionStatus,
    AuditEvent,
    ClientId,
    Conversation,
    Message,
    Prospect,
    ProspectId,
    SalesAction,
)


@runtime_checkable
class SalesRepository(Protocol):
    """Read/write access to one client's prospects, actions, history and audit trail.

    An instance is bound to a single client, so cross-client access is impossible by
    construction. Writes are batch-shaped because the MVP backend is rate-limited.
    Implementations must reject objects whose `client_id` differs from their own.
    """

    @property
    def client_id(self) -> ClientId: ...

    def list_prospects(self) -> Sequence[Prospect]: ...

    def get_prospect(self, prospect_id: ProspectId) -> Prospect | None: ...

    def find_prospect_by_contact(
        self, *, phone: str | None = None, email: str | None = None
    ) -> Prospect | None:
        """Prospect whose contact matches (used for inbound messages). Phone numbers are compared
        ignoring formatting (spaces, '+', dashes); emails ignoring case. None if no match."""
        ...

    def update_prospects(self, prospects: Sequence[Prospect]) -> None:
        """Persist the current state (status, stage, schedule, ...) of existing prospects.

        Raises `KeyError` for an unknown prospect; nothing is written in that case."""
        ...

    def list_actions(
        self, statuses: Collection[ActionStatus] | None = None
    ) -> Sequence[SalesAction]:
        """All actions, or only those in `statuses`."""
        ...

    def find_action_by_idempotency_key(self, key: str) -> SalesAction | None: ...

    def save_actions(self, actions: Sequence[SalesAction]) -> None:
        """Insert new actions and update existing ones, matched by `action_id`.

        Raises `ValueError`, writing nothing, if two different actions would share an
        `idempotency_key`."""
        ...

    def get_conversation(self, prospect_id: ProspectId) -> Conversation:
        """History for the prospect; an empty `Conversation` if there is none yet."""
        ...

    def append_messages(self, messages: Sequence[Message]) -> None:
        """Append-only. Raises `ValueError` for a duplicate `message_id` or a message whose
        `conversation_id` differs from the one already used for that prospect."""
        ...

    def append_audit_events(self, events: Sequence[AuditEvent]) -> None: ...
