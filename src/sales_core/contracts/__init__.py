"""Data models shared by Core, Brains and integrations.

Plain, immutable dataclasses. Only the standard library is imported here.
"""

from sales_core.contracts.action import SalesAction
from sales_core.contracts.audit import AuditEvent
from sales_core.contracts.brain import BrainDecision, BrainRequest, ClientContext
from sales_core.contracts.conversation import Conversation, Message, MessageDraft
from sales_core.contracts.enums import (
    SCHEDULABLE_ACTION_TYPES,
    ActionPriority,
    ActionStatus,
    Channel,
    ContactOrigin,
    DecisionType,
    Direction,
    FactSource,
    FactStatus,
    LLMRole,
    ProspectStatus,
    SalesActionType,
    SendStatus,
)
from sales_core.contracts.fact import Fact, FactEvidence, fact_identity
from sales_core.contracts.ids import ActionId, ClientId, ConversationId, MessageId, ProspectId
from sales_core.contracts.llm import LLMMessage, LLMRequest, LLMResponse
from sales_core.contracts.messaging import InboundEvent, InboundMessage, OutboundMessage, SendResult
from sales_core.contracts.prospect import ContactInfo, Prospect, Scheduling

__all__ = [
    "SCHEDULABLE_ACTION_TYPES",
    "ActionId",
    "ActionPriority",
    "ActionStatus",
    "AuditEvent",
    "BrainDecision",
    "BrainRequest",
    "Channel",
    "ClientContext",
    "ClientId",
    "ContactInfo",
    "ContactOrigin",
    "Conversation",
    "ConversationId",
    "DecisionType",
    "Direction",
    "Fact",
    "FactEvidence",
    "FactSource",
    "FactStatus",
    "InboundEvent",
    "InboundMessage",
    "LLMMessage",
    "LLMRequest",
    "LLMResponse",
    "LLMRole",
    "Message",
    "MessageDraft",
    "MessageId",
    "OutboundMessage",
    "Prospect",
    "ProspectId",
    "ProspectStatus",
    "SalesAction",
    "SalesActionType",
    "Scheduling",
    "SendResult",
    "SendStatus",
    "fact_identity",
]
