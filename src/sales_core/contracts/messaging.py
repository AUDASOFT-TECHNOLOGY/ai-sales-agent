"""Contracts for the outbound messaging channel."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sales_core.contracts._validation import freeze_mapping, require_aware, require_text
from sales_core.contracts.conversation import MessageDraft
from sales_core.contracts.enums import Channel, SendStatus
from sales_core.contracts.ids import ClientId, ProspectId

# A transient failure that sent nothing and says "not now" (an allowance used up): the Engine keeps
# the action approved and does not count the try. Carried in `SendResult.metadata`.
DEFERRED_KEY = "deferred"
DEFERRED_VALUE = "true"


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboundMessage:
    """A fully resolved message, ready for a channel to deliver."""

    client_id: ClientId
    prospect_id: ProspectId
    channel: Channel
    recipient: str  # channel-specific address (phone number, email, ...)
    draft: MessageDraft
    idempotency_key: str  # channels should not deliver the same key twice
    # True when this opens a conversation the person has not started themselves (the first message
    # to someone who never wrote to us): a channel that rations such messages counts it.
    first_contact: bool = False

    def __post_init__(self) -> None:
        require_text(self.client_id, "client_id")
        require_text(self.prospect_id, "prospect_id")
        require_text(self.recipient, "recipient")
        require_text(self.idempotency_key, "idempotency_key")


@dataclass(frozen=True, slots=True, kw_only=True)
class InboundMessage:
    """A message received from a prospect, as parsed by a channel adapter or webhook."""

    channel: Channel
    sender: str  # channel-specific address (phone number, email, ...)
    external_id: str  # provider message id; the key used to de-duplicate webhook redeliveries
    text: str
    timestamp: datetime
    metadata: Mapping[str, str] = field(default_factory=dict)
    # Who the sender says they are, set ONLY by an adapter that has authenticated the claim (never
    # from message text). A channel whose senders cannot be found by an address we already hold
    # (Telegram: a chat id) uses them to attach the sender to the prospect we meant to reach.
    linked_prospect_id: str | None = None  # an invitation that only we could have issued
    shared_phone: str | None = None  # a number the platform confirmed belongs to the sender

    def __post_init__(self) -> None:
        require_text(self.sender, "sender")
        require_text(self.external_id, "external_id")
        require_aware(self.timestamp, "timestamp")
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True, kw_only=True)
class InboundEvent:
    """An inbound message plus the key that says which client account it was addressed to."""

    routing_key: str  # e.g. the receiving phone-number id; mapped to a client by the app layer
    message: InboundMessage

    def __post_init__(self) -> None:
        require_text(self.routing_key, "routing_key")


@dataclass(frozen=True, slots=True, kw_only=True)
class SendResult:
    """Outcome of a send attempt. Expected failures are reported here, not raised."""

    status: SendStatus
    timestamp: datetime
    external_id: str | None = None  # provider message id when accepted
    error_code: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_aware(self.timestamp, "timestamp")
        if self.status is not SendStatus.ACCEPTED:
            require_text(self.error_message or "", "error_message")
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))

    @property
    def ok(self) -> bool:
        return self.status is SendStatus.ACCEPTED

    @property
    def retryable(self) -> bool:
        return self.status is SendStatus.FAILED_TRANSIENT
