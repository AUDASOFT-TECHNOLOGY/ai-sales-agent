"""Port for who is in charge of a conversation: the agent, or a person who took over.

While a person is in charge (mode `human`), the Engine sends nothing on its own to that prospect: no
reply, no follow-up, no reminder, no report of a follow-up. Only a message written by that person
goes out. A conversation can be in `human` mode **awaiting** a person (the Brain handed it over and
nobody took it yet: `taken_by` is None) or **taken** (a person said so).

An instance is bound to a single client, like the repository, so cross-client access is impossible
by construction. Implementations must be durable: a restart must not give a conversation back to the
agent.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sales_core.contracts import ClientId, ProspectId


class ControlMode(StrEnum):
    AGENT = "agent"
    HUMAN = "human"


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationControl:
    client_id: ClientId
    prospect_id: ProspectId
    mode: ControlMode
    taken_by: str | None = None  # None in human mode: handed over by the agent, awaiting a person
    taken_at: datetime | None = None  # when the human mode began
    reason: str | None = None
    released_at: datetime | None = None  # when the agent got the conversation back

    @property
    def is_human(self) -> bool:
        return self.mode is ControlMode.HUMAN

    @property
    def awaiting_human(self) -> bool:
        return self.is_human and self.taken_by is None


@runtime_checkable
class ConversationControlStore(Protocol):
    @property
    def client_id(self) -> ClientId: ...

    def get(self, prospect_id: ProspectId) -> ConversationControl | None:
        """None: the agent has always been in charge of this conversation."""
        ...

    def humans(self) -> frozenset[ProspectId]:
        """Every prospect whose conversation is in human mode (awaiting or taken)."""
        ...

    def await_human(
        self, prospect_id: ProspectId, *, reason: str | None, at: datetime
    ) -> ConversationControl:
        """The agent hands the conversation over. Idempotent: a conversation already in human mode
        keeps who took it and when."""
        ...

    def take(
        self, prospect_id: ProspectId, *, by: str, reason: str | None, at: datetime
    ) -> ConversationControl:
        """A person takes the conversation (also from `awaiting`). Idempotent for the same person;
        taking over from another person replaces them."""
        ...

    def release(self, prospect_id: ProspectId, *, at: datetime) -> ConversationControl | None:
        """Give the conversation back to the agent. None if it was never in human mode."""
        ...
