"""SalesAction: one unit of daily commercial work selected for a prospect."""

from dataclasses import dataclass
from datetime import datetime

from sales_core.contracts._validation import require_aware, require_text
from sales_core.contracts.conversation import MessageDraft
from sales_core.contracts.enums import (
    SCHEDULABLE_ACTION_TYPES,
    ActionPriority,
    ActionStatus,
    Channel,
    SalesActionType,
)
from sales_core.contracts.ids import ActionId, ClientId, LaneId, ProspectId


@dataclass(frozen=True, slots=True, kw_only=True)
class SalesAction:
    action_id: ActionId
    client_id: ClientId
    prospect_id: ProspectId
    action_type: SalesActionType
    due_at: datetime
    idempotency_key: str  # same key => same real-world action; guards against double sends
    priority: ActionPriority = ActionPriority.NORMAL
    status: ActionStatus = ActionStatus.PLANNED
    draft: MessageDraft | None = None  # filled once a Brain has proposed content
    attempts: int = 0
    last_error: str | None = None
    # What the prospect becomes once this action is SENT. Persisted with the action so the Brain's
    # proposal survives approval delays and restarts, and a human can edit it in the Sheet.
    # `next_stage=None` keeps the current stage; no next action clears the prospect's schedule.
    next_stage: str | None = None
    next_action_type: SalesActionType | None = None
    next_action_due_at: datetime | None = None
    # Where this action was meant to go, fixed when it was planned (or answered). A lane that is
    # reconfigured later never redirects work already in flight. None: the client has no lanes.
    lane_id: LaneId | None = None
    channel: Channel | None = None

    def __post_init__(self) -> None:
        require_text(self.action_id, "action_id")
        require_text(self.client_id, "client_id")
        require_text(self.prospect_id, "prospect_id")
        require_text(self.idempotency_key, "idempotency_key")
        require_aware(self.due_at, "due_at")
        if self.attempts < 0:
            raise ValueError("attempts must be >= 0")
        if (self.next_action_type is None) != (self.next_action_due_at is None):
            raise ValueError("next_action_type and next_action_due_at must be set together")
        if (
            self.next_action_type is not None
            and self.next_action_type not in SCHEDULABLE_ACTION_TYPES
        ):
            raise ValueError(f"{self.next_action_type} cannot be scheduled as a next action")
        require_aware(self.next_action_due_at, "next_action_due_at")
