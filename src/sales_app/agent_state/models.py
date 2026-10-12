"""What a client has decided about its own agent: run it, or keep it paused.

This is the Client's switch, kept apart from the Admin's `enabled` (in `client.yaml`). The two never
mix: the Admin's decision is a ceiling, and this one only ever chooses within it. A client with no
record has never started its agent.
"""

from dataclasses import dataclass
from datetime import datetime


class AgentStateStoreCorruptError(RuntimeError):
    """Stored agent states cannot be trusted. The message names no value."""


class AgentStateStoreUnavailableError(RuntimeError):
    """The store cannot be reached (a database down, a missing table). Says nothing of the cause."""


@dataclass(frozen=True, slots=True)
class AgentState:
    client_id: str
    running: bool  # False: the client paused it
    changed_at: datetime
    changed_by: str  # `client:<id>` or an operator's name: who flipped the switch last

    def __post_init__(self) -> None:
        if not self.client_id:
            raise ValueError("an agent state needs a client id")
        if self.changed_at.tzinfo is None:
            raise ValueError("changed_at must be timezone-aware")
