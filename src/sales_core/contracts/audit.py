"""Audit trail entries: what the Core (or a human) decided and did."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sales_core.contracts._validation import freeze_mapping, require_aware, require_text
from sales_core.contracts.ids import ActionId, ClientId, ProspectId


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditEvent:
    timestamp: datetime
    client_id: ClientId
    event_type: str  # dotted code such as "action.sent"; the Core owns the vocabulary
    actor: str  # e.g. "core", "brain", "human:<name>"
    prospect_id: ProspectId | None = None
    action_id: ActionId | None = None
    details: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_aware(self.timestamp, "timestamp")
        require_text(self.client_id, "client_id")
        require_text(self.event_type, "event_type")
        require_text(self.actor, "actor")
        object.__setattr__(self, "details", freeze_mapping(self.details))
