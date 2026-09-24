"""Conversation history: messages exchanged with a prospect, and drafts not yet sent."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sales_core.contracts._validation import freeze_mapping, require_aware, require_text
from sales_core.contracts.enums import Channel, ContactOrigin, Direction
from sales_core.contracts.ids import ClientId, ConversationId, MessageId, ProspectId


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageDraft:
    """Content a Brain wants to send. Channel and recipient are resolved by the Core."""

    text: str
    language: str | None = None  # declared by the Brain, opaque to the Core
    template_id: str | None = None  # channel-approved template, when the channel requires one
    template_params: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.template_id is None:
            require_text(self.text, "text")
        object.__setattr__(self, "template_params", freeze_mapping(self.template_params))


@dataclass(frozen=True, slots=True, kw_only=True)
class Message:
    """One sent or received message."""

    message_id: MessageId
    client_id: ClientId
    prospect_id: ProspectId
    conversation_id: ConversationId
    direction: Direction
    channel: Channel
    text: str  # may be empty for inbound non-text content; details go in `metadata`
    timestamp: datetime
    external_id: str | None = None  # id assigned by the channel provider
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_text(self.message_id, "message_id")
        require_text(self.client_id, "client_id")
        require_text(self.prospect_id, "prospect_id")
        require_text(self.conversation_id, "conversation_id")
        require_aware(self.timestamp, "timestamp")
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True, kw_only=True)
class Conversation:
    """History for one prospect, oldest message first. An empty history is valid."""

    conversation_id: ConversationId
    client_id: ClientId
    prospect_id: ProspectId
    messages: tuple[Message, ...] = ()
    origin: ContactOrigin = ContactOrigin.UNKNOWN  # who started it; UNKNOWN for older history

    def __post_init__(self) -> None:
        require_text(self.conversation_id, "conversation_id")
        require_text(self.client_id, "client_id")
        require_text(self.prospect_id, "prospect_id")
        object.__setattr__(self, "messages", tuple(self.messages))
        for message in self.messages:
            if (
                message.conversation_id != self.conversation_id
                or message.client_id != self.client_id
                or message.prospect_id != self.prospect_id
            ):
                raise ValueError(
                    f"message {message.message_id!r} does not belong to "
                    f"conversation {self.conversation_id!r}"
                )
