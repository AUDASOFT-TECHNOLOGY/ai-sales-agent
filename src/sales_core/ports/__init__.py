"""Interfaces implemented outside the Core (SalesRepository, MessagingChannel, LLMProvider, Brain, Clock, WebhookParser)."""

from sales_core.ports.brain import Brain, BrainFactory
from sales_core.ports.channel import MessagingChannel
from sales_core.ports.clock import Clock
from sales_core.ports.llm import LLMProvider
from sales_core.ports.sales_repository import SalesRepository
from sales_core.ports.webhook import InvalidSignatureError, MalformedPayloadError, WebhookParser

__all__ = [
    "Brain",
    "BrainFactory",
    "Clock",
    "InvalidSignatureError",
    "LLMProvider",
    "MalformedPayloadError",
    "MessagingChannel",
    "SalesRepository",
    "WebhookParser",
]
