"""A messaging channel that can never send. Used by dry-run engines."""

from sales_core.contracts import Channel, OutboundMessage, SendResult


class ChannelDisabledError(RuntimeError):
    """Raised if anything tries to send through a disabled channel."""


class DisabledChannel:
    """Implements `sales_core.ports.MessagingChannel`, but `send` always raises.

    A dry-run Engine never sends. Giving it this channel, instead of a real one, makes that a
    property of the wiring: even a bug could not deliver a message.
    """

    def __init__(self, channel: Channel) -> None:
        self._channel = channel

    @property
    def channel(self) -> Channel:
        return self._channel

    def send(self, message: OutboundMessage) -> SendResult:
        raise ChannelDisabledError(f"the {self._channel} channel is disabled (dry run)")
