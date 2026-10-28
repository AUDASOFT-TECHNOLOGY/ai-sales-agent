"""Telegram from the client's own account: the channel that sends and the inbox that reads.

Nothing here talks to Telegram: a fake account (`FakeAccountClient`) stands for it. It proves what the
code asks of the account and how it reads the failures the port names, not that a real account
behaves so.
"""

from datetime import UTC, datetime

import pytest
from factories import NOW
from telegram_account_double import FakeAccountClient

from sales_core.contracts import Channel, MessageDraft, OutboundMessage, SendStatus
from sales_core.contracts.messaging import DEFERRED_KEY
from sales_core.ports import MessagingChannel
from sales_integrations.fakes import FakeClock
from sales_integrations.telegram import TelegramAccountChannel, TelegramAccountInbox
from sales_integrations.telegram.account import InboxNotReadyError

PEER = "424242"


def outbound(text="Hello there", recipient=PEER, first_contact=False) -> OutboundMessage:
    return OutboundMessage(
        client_id="client-a",
        prospect_id="p1",
        channel=Channel.TELEGRAM,
        recipient=recipient,
        draft=MessageDraft(text=text),
        idempotency_key="k1",
        first_contact=first_contact,
    )


class Events:
    def __init__(self):
        self.limited = False
        self.auth_lost = 0

    def channel(self, client, **kw):
        return TelegramAccountChannel(
            client,
            clock=FakeClock(NOW),
            limited=lambda: self.limited,
            on_limited=lambda: setattr(self, "limited", True),
            on_auth_lost=lambda: setattr(self, "auth_lost", self.auth_lost + 1),
            **kw,
        )


class TestSending:
    def test_it_is_a_telegram_messaging_channel(self):
        channel = Events().channel(FakeAccountClient())
        assert isinstance(channel, MessagingChannel) and channel.channel is Channel.TELEGRAM

    def test_a_message_is_sent_to_the_account_and_named_after_it(self):
        account = FakeAccountClient()
        result = Events().channel(account).send(outbound())
        assert result.ok and account.sent == [(PEER, "Hello there")]
        assert result.external_id.startswith(f"{PEER}:")

    def test_a_long_message_goes_in_pieces_and_is_named_by_the_first(self):
        account = FakeAccountClient()
        result = Events().channel(account).send(outbound("x" * 5000))
        assert result.ok and len(account.sent) == 2 and all(p == PEER for p, _ in account.sent)

    def test_a_recipient_that_is_not_an_account_id_is_refused_before_anything_is_sent(self):
        account = FakeAccountClient()
        result = Events().channel(account).send(outbound(recipient="+15550000001"))
        assert result.status is SendStatus.FAILED_PERMANENT and account.sent == []

    def test_a_person_who_cannot_be_written_to_is_a_permanent_failure(self):
        for kind in ("blocked", "not_found", "bad_phone"):
            account = FakeAccountClient()
            account.fail("send_text", kind)
            result = Events().channel(account).send(outbound())
            assert result.status is SendStatus.FAILED_PERMANENT, kind

    def test_telegram_not_being_reachable_is_transient(self):
        account = FakeAccountClient()
        account.fail("send_text", "unreachable")
        result = Events().channel(account).send(outbound())
        assert result.status is SendStatus.FAILED_TRANSIENT and DEFERRED_KEY not in result.metadata

    def test_a_flood_wait_is_deferred_not_counted(self):
        account = FakeAccountClient()
        account.fail("send_text", "flood_wait", retry_after=300)
        result = Events().channel(account).send(outbound())
        assert result.status is SendStatus.FAILED_TRANSIENT
        assert result.metadata[DEFERRED_KEY] and "300" in result.error_message

    def test_a_session_telegram_ended_asks_the_person_to_sign_in_again(self):
        events, account = Events(), FakeAccountClient()
        account.fail("send_text", "auth_lost")
        result = events.channel(account).send(outbound())
        # transient: the person is not at fault, the Engine must not pause them
        assert result.status is SendStatus.FAILED_TRANSIENT and events.auth_lost == 1

    def test_peer_flood_stops_every_first_message_but_not_the_replies(self):
        events, account = Events(), FakeAccountClient()
        account.fail("send_text", "peer_flood")
        channel = events.channel(account)
        first = channel.send(outbound(first_contact=True))
        assert first.status is SendStatus.FAILED_TRANSIENT and events.limited
        assert first.metadata[DEFERRED_KEY]
        # from now on a first message is not even tried ...
        account.sent.clear()
        again = channel.send(outbound(first_contact=True))
        assert not again.ok and account.sent == []
        # ... while an answer to someone who wrote to us still goes
        assert channel.send(outbound("Thanks for writing", first_contact=False)).ok
        assert account.sent == [(PEER, "Thanks for writing")]

    def test_a_timeout_during_a_send_is_not_a_failure_it_is_unknown(self):
        class Hangs(FakeAccountClient):
            def send_text(self, peer, text):
                raise TimeoutError

        with pytest.raises(TimeoutError):
            Events().channel(Hangs()).send(outbound())

    def test_a_failure_after_the_first_piece_is_unknown_not_failed(self):
        account = FakeAccountClient()
        channel = Events().channel(account)
        original = account.send_text
        calls = []

        def second_piece_fails(peer, text):
            calls.append(text)
            if len(calls) == 2:
                account.fail("send_text", "unreachable")
            return original(peer, text)

        account.send_text = second_piece_fails
        with pytest.raises(RuntimeError, match="in part"):
            channel.send(outbound("x" * 5000))

    def test_nothing_of_the_text_or_the_person_is_in_a_failure(self):
        account = FakeAccountClient()
        account.fail("send_text", "blocked")
        result = Events().channel(account).send(outbound("a very private offer"))
        assert "private" not in result.error_message and PEER not in result.error_message


class MemoryPositions:
    def __init__(self):
        self.rows = {}

    def load(self, mailbox):
        return self.rows.get(mailbox)

    def save(self, mailbox, uidvalidity, next_uid, updated_at):
        held = self.rows.get(mailbox)
        if held is None or next_uid > held[1]:
            self.rows[mailbox] = (uidvalidity, next_uid, updated_at)


def inbox(account, positions, *, known=("111",), **kw):
    reader = TelegramAccountInbox(account, "900001", clock=FakeClock(NOW), needs_cursor=True, **kw)
    reader.use_cursor(positions)

    def accept(channel, sender, phone=None):
        assert channel is Channel.TELEGRAM
        return sender in known

    return reader, accept


class TestReading:
    def test_the_first_read_only_notes_where_the_account_stands(self):
        account, positions = FakeAccountClient(), MemoryPositions()
        account.receive("111", "written before the client connected", message_id=40)
        reader, accept = inbox(account, positions)
        assert reader.fetch(10, accept) == []
        reader.close()
        (held,) = positions.rows.values()
        assert held[1] == 41 and account.reads == []  # nothing was read, only the newest id

    def test_only_prospects_are_read_and_their_messages_are_named_after_the_account(self):
        account, positions = FakeAccountClient(), MemoryPositions()
        reader, accept = inbox(account, positions)
        reader.fetch(10, accept)
        reader.close()
        account.receive("111", "I am interested", message_id=50, phone="+15550000001")
        account.receive("222", "a friend of the owner", message_id=51)  # not a prospect
        later, accept = inbox(account, positions)
        (item,) = later.fetch(10, accept)
        message = item.message
        assert message.channel is Channel.TELEGRAM and message.sender == "111"
        assert message.external_id == "111:50" and message.text == "I am interested"
        assert message.shared_phone == "+15550000001"
        assert message.metadata["sender_name"] == "Person 111"

    def test_a_stranger_is_never_handed_over(self):
        account, positions = FakeAccountClient(), MemoryPositions()
        reader, accept = inbox(account, positions)
        reader.fetch(10, accept)
        reader.close()
        account.receive("222", "private", message_id=60)
        later, accept = inbox(account, positions)
        assert later.fetch(10, accept) == []
        later.close()
        # and the position went past it: it is not offered again
        assert next(iter(positions.rows.values()))[1] == 61

    def test_a_message_is_offered_again_until_it_is_acknowledged(self):
        account, positions = FakeAccountClient(), MemoryPositions()
        reader, accept = inbox(account, positions)
        reader.fetch(10, accept)
        reader.close()
        account.receive("111", "one", message_id=70)
        account.receive("111", "two", message_id=71)

        first, accept = inbox(account, positions)
        _, b = first.fetch(10, accept)
        first.acknowledge(b.handle)  # the second was stored, the first failed
        first.close()

        second, accept = inbox(account, positions)
        again = second.fetch(10, accept)
        assert [m.message.text for m in again] == ["one", "two"]  # the Engine de-duplicates
        for item in again:
            second.acknowledge(item.handle)
        second.close()
        third, accept = inbox(account, positions)
        assert third.fetch(10, accept) == []

    def test_a_long_backlog_is_read_in_batches_without_skipping(self):
        account, positions = FakeAccountClient(), MemoryPositions()
        reader, accept = inbox(account, positions)
        reader.fetch(10, accept)
        reader.close()
        for i in range(5):
            account.receive("111", f"m{i}", message_id=80 + i)
        seen = []
        for _ in range(4):
            batch, accept = inbox(account, positions)
            items = batch.fetch(2, accept)
            for item in items:
                seen.append(item.message.text)
                batch.acknowledge(item.handle)
            batch.close()
        assert seen == ["m0", "m1", "m2", "m3", "m4"]

    def test_a_position_only_moves_forward(self):
        positions = MemoryPositions()
        positions.save("k", 1, 10, datetime.now(UTC))
        positions.save("k", 1, 5, datetime.now(UTC))
        assert positions.load("k")[1] == 10

    def test_two_accounts_do_not_share_a_position(self):
        one = TelegramAccountInbox(FakeAccountClient(), "900001")
        two = TelegramAccountInbox(FakeAccountClient(), "900002")
        assert one.cursor_key != two.cursor_key
        assert "900001" not in one.cursor_key  # the position names the account only by a digest

    def test_it_refuses_to_read_without_a_place_to_keep_its_position(self):
        reader = TelegramAccountInbox(FakeAccountClient(), "900001")
        with pytest.raises(InboxNotReadyError):
            reader.fetch(10, lambda *a: True)

    def test_it_refuses_to_read_without_being_told_whom_to_accept(self):
        reader, _ = inbox(FakeAccountClient(), MemoryPositions())
        with pytest.raises(ValueError):
            reader.fetch(10, None)

    def test_a_session_telegram_ended_is_reported_and_nothing_is_read(self):
        events, account = Events(), FakeAccountClient()
        account.fail("newest_message_id", "auth_lost")
        reader, accept = inbox(account, MemoryPositions(), on_auth_lost=lambda: events.__init__())
        with pytest.raises(InboxNotReadyError):
            reader.fetch(10, accept)

    def test_phone_lookups_are_made_through_the_account(self):
        class Engine:
            def link_telegram_by_phone(self, resolve, *, limit):
                self.answer, self.limit = resolve("+15550000001", "Lina"), limit
                return 1

        account = FakeAccountClient(people={"+15550000001": "333"})
        reader, _ = inbox(account, MemoryPositions(), link_limit=3)
        engine = Engine()
        assert reader.link_by_phone(engine) == 1
        assert engine.answer == "333" and engine.limit == 3 and account.lookups == ["+15550000001"]
        idle, _ = inbox(account, MemoryPositions())  # no lookups configured
        assert idle.link_by_phone(engine) == 0
