"""SystemClock and DisabledChannel."""

from datetime import UTC, datetime, timedelta

import pytest
from factories import CLIENT, PROSPECT

from sales_core.contracts import Channel, MessageDraft, OutboundMessage
from sales_core.ports import Clock, MessagingChannel
from sales_integrations.system import ChannelDisabledError, DisabledChannel, SystemClock


def test_system_clock_is_timezone_aware_and_current():
    clock = SystemClock()
    assert isinstance(clock, Clock)
    now = clock.now()
    assert now.tzinfo is not None and now.utcoffset() == timedelta(0)
    assert abs(datetime.now(UTC) - now) < timedelta(seconds=5)


def test_disabled_channel_reports_its_kind_and_refuses_to_send():
    channel = DisabledChannel(Channel.WHATSAPP)
    assert isinstance(channel, MessagingChannel) and channel.channel is Channel.WHATSAPP
    message = OutboundMessage(
        client_id=CLIENT,
        prospect_id=PROSPECT,
        channel=Channel.WHATSAPP,
        recipient="+1",
        draft=MessageDraft(text="hi"),
        idempotency_key="k",
    )
    with pytest.raises(ChannelDisabledError, match="disabled"):
        channel.send(message)
