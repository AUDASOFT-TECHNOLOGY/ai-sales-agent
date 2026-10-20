"""The reading port of a client's prospects and conversations.

Independent of the agent: an implementation reads what the platform has stored and never needs the
Engine to be loaded. One instance serves one client; no method takes a client id.
"""

from typing import Protocol

from sales_app.read_model.models import (
    ConversationDetail,
    ConversationItem,
    MessageOrder,
    Page,
    ProspectItem,
    ProspectListQuery,
)


class ReadModelUnavailableError(Exception):
    """The store cannot be read. Carries no detail: it could hold an id, a name or a number."""


class ProspectReadModel(Protocol):
    def is_populated(self) -> bool:
        """Whether the client has any prospect in the read model (otherwise the caller falls back
        to the Engine's view, which also fills the read model as a side effect)."""
        ...

    def list_prospects(self, query: ProspectListQuery) -> Page[ProspectItem]: ...

    def list_conversations(self, query: ProspectListQuery) -> Page[ConversationItem]:
        """Only prospects with at least one stored message; `RECENT` sorts by the last message."""
        ...

    def conversation(
        self, prospect_id: str, *, limit: int, offset: int, order: MessageOrder
    ) -> ConversationDetail | None:
        """None for a prospect the client does not have (the same answer for another client's)."""
        ...
