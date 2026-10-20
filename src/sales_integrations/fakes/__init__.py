"""In-memory implementations of the Core ports for tests and local dry runs.

Depend only on the standard library and `sales_core`. No external services, no database.
"""

from sales_integrations.fakes.fake_clock import FakeClock
from sales_integrations.fakes.fake_conversation_control import FakeConversationControl
from sales_integrations.fakes.fake_llm_provider import FakeLLMProvider
from sales_integrations.fakes.fake_messaging_channel import FakeMessagingChannel
from sales_integrations.fakes.fake_sales_repository import FakeSalesRepository

__all__ = [
    "FakeClock",
    "FakeConversationControl",
    "FakeLLMProvider",
    "FakeMessagingChannel",
    "FakeSalesRepository",
]
