"""Prospect: who we may contact, where they stand, and when we should act next."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sales_core.contracts._validation import freeze_mapping, require_aware, require_text
from sales_core.contracts.enums import Channel, ProspectStatus, SalesActionType
from sales_core.contracts.ids import ClientId, ConversationId, ProspectId


@dataclass(frozen=True, slots=True, kw_only=True)
class ContactInfo:
    phone: str | None = None  # E.164 expected; normalisation is the adapter's job
    email: str | None = None
    # The Telegram chat a person opened with the client's bot. A bot cannot write first, so this
    # is empty until the person starts the conversation; it is the only Telegram address there is.
    telegram_chat_id: str | None = None
    preferred_channel: Channel | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Scheduling:
    """When the prospect was last touched and what is due next."""

    next_action_type: SalesActionType | None = None
    next_action_due_at: datetime | None = None
    last_contacted_at: datetime | None = None
    last_inbound_at: datetime | None = None
    follow_up_count: int = 0

    def __post_init__(self) -> None:
        if (self.next_action_type is None) != (self.next_action_due_at is None):
            raise ValueError("next_action_type and next_action_due_at must be set together")
        require_aware(self.next_action_due_at, "next_action_due_at")
        require_aware(self.last_contacted_at, "last_contacted_at")
        require_aware(self.last_inbound_at, "last_inbound_at")
        if self.follow_up_count < 0:
            raise ValueError("follow_up_count must be >= 0")


@dataclass(frozen=True, slots=True, kw_only=True)
class Prospect:
    client_id: ClientId
    prospect_id: ProspectId
    display_name: str
    contact: ContactInfo = field(default_factory=ContactInfo)
    status: ProspectStatus = ProspectStatus.NEW
    stage: str | None = None  # client-specific funnel label, owned by the Brain; opaque to the Core
    locale: str | None = None  # e.g. "fr", "ar-TN"; data about the prospect, not a Core setting
    conversation_id: ConversationId | None = None  # reference to the history, not the history
    schedule: Scheduling = field(default_factory=Scheduling)
    attributes: Mapping[str, str] = field(default_factory=dict)  # extra source columns, opaque

    def __post_init__(self) -> None:
        require_text(self.client_id, "client_id")
        require_text(self.prospect_id, "prospect_id")
        object.__setattr__(self, "attributes", freeze_mapping(self.attributes))
