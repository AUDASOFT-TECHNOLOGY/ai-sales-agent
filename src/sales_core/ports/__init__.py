"""Interfaces implemented outside the Core (SalesRepository, FactsReader, FactsWriter, MessagingChannel, LLMProvider, Brain, Clock, WebhookParser)."""

from sales_core.ports.activity import NO_ACTIVITY, ActivityReporter, NoActivity, doing
from sales_core.ports.brain import Brain, BrainFactory, BrainSettingsSpec
from sales_core.ports.channel import MessagingChannel
from sales_core.ports.clock import Clock
from sales_core.ports.conversation_control import (
    ControlMode,
    ConversationControl,
    ConversationControlStore,
)
from sales_core.ports.facts import FactsReader, FactsWriter
from sales_core.ports.llm import (
    LLMAuthError,
    LLMError,
    LLMModelError,
    LLMOutputError,
    LLMProvider,
    LLMRateLimitError,
    LLMTimeoutError,
)
from sales_core.ports.sales_repository import SalesRepository
from sales_core.ports.webhook import InvalidSignatureError, MalformedPayloadError, WebhookParser

__all__ = [
    "NO_ACTIVITY",
    "ActivityReporter",
    "Brain",
    "BrainFactory",
    "BrainSettingsSpec",
    "Clock",
    "ControlMode",
    "ConversationControl",
    "ConversationControlStore",
    "FactsReader",
    "FactsWriter",
    "InvalidSignatureError",
    "LLMAuthError",
    "LLMError",
    "LLMModelError",
    "LLMOutputError",
    "LLMProvider",
    "LLMRateLimitError",
    "LLMTimeoutError",
    "MalformedPayloadError",
    "MessagingChannel",
    "NoActivity",
    "SalesRepository",
    "WebhookParser",
    "doing",
]
