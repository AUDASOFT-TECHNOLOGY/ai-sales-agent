"""Interfaces implemented outside the Core (SalesRepository, FactsReader, FactsWriter, MessagingChannel, LLMProvider, Brain, Clock, WebhookParser)."""

from sales_core.ports.brain import Brain, BrainFactory, BrainSettingsSpec
from sales_core.ports.channel import MessagingChannel
from sales_core.ports.clock import Clock
from sales_core.ports.facts import FactsReader, FactsWriter
from sales_core.ports.llm import LLMProvider
from sales_core.ports.sales_repository import SalesRepository
from sales_core.ports.webhook import InvalidSignatureError, MalformedPayloadError, WebhookParser

__all__ = [
    "Brain",
    "BrainFactory",
    "BrainSettingsSpec",
    "Clock",
    "FactsReader",
    "FactsWriter",
    "InvalidSignatureError",
    "LLMProvider",
    "MalformedPayloadError",
    "MessagingChannel",
    "SalesRepository",
    "WebhookParser",
]
