"""Port for turning a provider's webhook request into inbound events."""

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from sales_core.contracts import InboundEvent


class InvalidSignatureError(Exception):
    """The request is not authentic (missing or wrong signature)."""


class MalformedPayloadError(Exception):
    """The body is not something this provider would send."""


@runtime_checkable
class WebhookParser(Protocol):
    """Authenticates and parses one provider's webhooks. Knows nothing about clients."""

    def handshake(self, query: Mapping[str, str]) -> str | None:
        """Answer the provider's subscription handshake; None if the request must be refused."""
        ...

    def parse(self, body: bytes, headers: Mapping[str, str]) -> Sequence[InboundEvent]:
        """Verify authenticity and return the inbound messages in `body` (possibly none).

        Raises `InvalidSignatureError` or `MalformedPayloadError`.
        """
        ...
