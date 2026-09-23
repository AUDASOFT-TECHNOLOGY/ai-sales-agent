"""Process-level adapters: the wall clock and a channel that refuses to send."""

from sales_integrations.system.clock import SystemClock
from sales_integrations.system.disabled_channel import ChannelDisabledError, DisabledChannel

__all__ = ["ChannelDisabledError", "DisabledChannel", "SystemClock"]
