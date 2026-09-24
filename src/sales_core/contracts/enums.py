"""Closed vocabularies of the Core.

String values are part of the persisted contract (they end up in Sheet cells): do not rename.
Client-specific funnel stages are NOT enums here; they are free-form labels owned by a Brain.
"""

from enum import IntEnum, StrEnum


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class ProspectStatus(StrEnum):
    """Core-level lifecycle. Finer, client-specific stages live in `Prospect.stage`."""

    NEW = "new"
    ACTIVE = "active"
    PAUSED = "paused"  # waiting for a human; the Core must not contact
    OPTED_OUT = "opted_out"
    CLOSED = "closed"


class SalesActionType(StrEnum):
    FIRST_CONTACT = "first_contact"
    FOLLOW_UP = "follow_up"
    REMINDER = "reminder"
    CALLBACK = "callback"
    REPLY = "reply"


# Types a Brain (or a human editing the Sheet) may schedule as a prospect's next action.
SCHEDULABLE_ACTION_TYPES = frozenset(
    {SalesActionType.FOLLOW_UP, SalesActionType.REMINDER, SalesActionType.CALLBACK}
)


class ActionStatus(StrEnum):
    PLANNED = "planned"  # selected by the planner, no message yet
    PROPOSED = "proposed"  # draft ready, awaiting approval
    APPROVED = "approved"
    REJECTED = "rejected"
    SENDING = "sending"  # written before the channel call (idempotency guard)
    SENT = "sent"
    COMPLETED = "completed"  # resolved without sending (schedule, handoff, close, no action)
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActionPriority(IntEnum):
    LOW = 10
    NORMAL = 20
    HIGH = 30
    URGENT = 40


class DecisionType(StrEnum):
    SEND_MESSAGE = "send_message"
    SCHEDULE_FOLLOW_UP = "schedule_follow_up"  # no message now, act again later
    HANDOFF_TO_HUMAN = "handoff_to_human"
    CLOSE = "close"
    NO_ACTION = "no_action"


class SendStatus(StrEnum):
    ACCEPTED = "accepted"
    FAILED_TRANSIENT = "failed_transient"  # safe to retry
    FAILED_PERMANENT = "failed_permanent"


class LLMRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ContactOrigin(StrEnum):
    """Who started a conversation. History that cannot tell stays UNKNOWN, never guessed."""

    OUTBOUND = "outbound"  # initiated by the Sales Agent
    INBOUND = "inbound"  # initiated by the prospect
    UNKNOWN = "unknown"  # existing data that does not say


class FactStatus(StrEnum):
    """How much is known about a fact. Nothing in the Core moves a fact from one to another."""

    CONFIRMED = "confirmed"  # stated or verified by a trustworthy source
    DISCOVERED = "discovered"  # learned from the conversation or data, not yet confirmed
    HYPOTHESIS = "hypothesis"  # an inference; may be used to guide, never to assert


class FactSource(StrEnum):
    """Where a fact came from."""

    PROSPECT = "prospect"  # the prospect said so
    CLIENT_DATA = "client_data"  # supplied by the client (lead file, CRM export)
    CONVERSATION = "conversation"  # read from the exchange without the prospect stating it outright
    SYSTEM = "system"  # imported or computed by the platform
    HUMAN = "human"  # entered or confirmed by an operator
    BRAIN_INFERENCE = "brain_inference"  # deduced by a Brain
