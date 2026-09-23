"""Interfaces implemented outside the Core (SalesRepository, MessagingChannel, LLMProvider, Brain, Clock)."""

from sales_core.ports.brain import Brain
from sales_core.ports.channel import MessagingChannel
from sales_core.ports.clock import Clock
from sales_core.ports.llm import LLMProvider
from sales_core.ports.sales_repository import SalesRepository

__all__ = ["Brain", "Clock", "LLMProvider", "MessagingChannel", "SalesRepository"]
