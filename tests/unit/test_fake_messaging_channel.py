from datetime import timedelta

from factories import CLIENT, NOW, PROSPECT

from sales_core.contracts import Channel, MessageDraft, OutboundMessage, SendResult, SendStatus
from sales_core.ports import MessagingChannel
from sales_integrations.fakes import FakeClock, FakeMessagingChannel


def _message(key: str = "k-1", channel: Channel = Channel.WHATSAPP) -> OutboundMessage:
    return OutboundMessage(
        client_id=CLIENT,
        prospect_id=PROSPECT,
        channel=channel,
        recipient="+10000000000",
        draft=MessageDraft(text="Hi"),
        idempotency_key=key,
    )


def test_satisfies_the_port_and_reports_its_channel():
    channel = FakeMessagingChannel(Channel.SMS)
    assert isinstance(channel, MessagingChannel)
    assert channel.channel is Channel.SMS


def test_accepts_by_default_with_unique_ids_and_clock_timestamps():
    clock = FakeClock(NOW)
    channel = FakeMessagingChannel(clock=clock)
    first = channel.send(_message("k-1"))
    clock.advance(timedelta(minutes=5))
    second = channel.send(_message("k-2"))
    assert first.ok and second.ok
    assert first.external_id != second.external_id
    assert first.timestamp == NOW
    assert second.timestamp == NOW + timedelta(minutes=5)


def test_records_sent_messages_in_order():
    channel = FakeMessagingChannel()
    a, b = _message("k-1"), _message("k-2")
    channel.send(a)
    channel.send(b)
    assert channel.sent == (a, b)
    assert channel.attempts == (a, b)


def test_same_idempotency_key_is_delivered_once_and_replays_the_result():
    channel = FakeMessagingChannel()
    first = channel.send(_message("k-1"))
    replay = channel.send(_message("k-1"))
    assert replay == first
    assert len(channel.sent) == 1
    assert len(channel.attempts) == 2


def test_fail_next_returns_a_transient_failure_without_raising():
    channel = FakeMessagingChannel()
    channel.fail_next(error_message="rate limited", error_code="429")
    result = channel.send(_message())
    assert not result.ok and result.retryable
    assert result.error_code == "429" and result.error_message == "rate limited"
    assert channel.sent == ()


def test_fail_next_can_be_permanent_and_repeated():
    channel = FakeMessagingChannel()
    channel.fail_next(transient=False, count=2)
    results = [channel.send(_message("a")), channel.send(_message("b"))]
    assert all(r.status is SendStatus.FAILED_PERMANENT for r in results)
    assert channel.send(_message("c")).ok


def test_a_failed_send_can_be_retried_with_the_same_key():
    channel = FakeMessagingChannel()
    channel.fail_next()
    assert not channel.send(_message("k-1")).ok
    assert channel.send(_message("k-1")).ok
    assert len(channel.sent) == 1


def test_queue_results_are_used_in_order_then_default_resumes():
    channel = FakeMessagingChannel()
    scripted = SendResult(status=SendStatus.ACCEPTED, timestamp=NOW, external_id="wamid.X")
    channel.queue_results(scripted)
    assert channel.send(_message("a")) == scripted
    assert channel.send(_message("b")).external_id != "wamid.X"


def test_replays_do_not_consume_scripted_results():
    channel = FakeMessagingChannel()
    channel.send(_message("k-1"))
    channel.fail_next()
    assert channel.send(_message("k-1")).ok  # replay, script untouched
    assert not channel.send(_message("k-2")).ok  # scripted failure hits the next new send


def test_wrong_channel_is_a_permanent_failure_not_an_exception():
    channel = FakeMessagingChannel(Channel.WHATSAPP)
    result = channel.send(_message(channel=Channel.EMAIL))
    assert result.status is SendStatus.FAILED_PERMANENT
    assert result.error_code == "unsupported_channel"
    assert channel.sent == ()
