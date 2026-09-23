"""Port for delivering messages to prospects (WhatsApp first, others later)."""

from typing import Protocol, runtime_checkable

from sales_core.contracts import Channel, OutboundMessage, SendResult


@runtime_checkable
class MessagingChannel(Protocol):
    @property
    def channel(self) -> Channel: ...

    def send(self, message: OutboundMessage) -> SendResult:
        """Attempt delivery. Expected failures (rejected, rate-limited, unreachable) are
        returned as a failed `SendResult`; only programming errors should raise."""
        ...
