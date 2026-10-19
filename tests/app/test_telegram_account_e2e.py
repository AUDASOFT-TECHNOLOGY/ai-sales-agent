"""Telegram from the client's own account, end to end below the HTTP layer.

Real: the Engine, `poll_inbox`, the account inbox and channel, the first-contact rationing over the
shared counter, the SQL reading position (SQLite stands in for PostgreSQL). Simulated: Telegram itself
(`FakeAccountClient`). This proves how the pieces fit and what leaves and what is read; it proves
nothing about a real account.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))


from datetime import timedelta

import pytest
from engine_harness import ScriptedBrain, build, prospect, send
from factories import NOW
from telegram_account_double import FakeAccountClient

from sales_app.inbox import poll_inbox
from sales_app.persistence.mail_cursor_rows import SqlMailCursorStore
from sales_app.send_cap import (
    FirstContactCapChannel,
    InMemorySendCapStore,
    InMemorySendCounter,
    SendLimits,
)
from sales_core.approval import ApprovalMode
from sales_core.contracts import ActionStatus, Channel, ContactInfo, Direction, ProspectStatus
from sales_integrations.telegram import TelegramAccountChannel, TelegramAccountInbox

COLUMN = "telegram_consent"
ACCOUNT_ID = "900001"
LINA, OMAR, STRANGER = "5001", "5002", "6666"


def person(pid, phone, *, consent="yes", account=None):
    return prospect(
        pid,
        contact=ContactInfo(phone=phone, telegram_chat_id=account),
        attributes={COLUMN: consent} if consent else {},
        display_name=pid.title(),
    )


class Room:
    """One client on its own Telegram account: Engine, rationed channel, inbox, and Telegram."""

    def __init__(self, sql_engine, *prospects, daily=5, brain=None):
        self.account = FakeAccountClient(
            user_id=ACCOUNT_ID,
            people={"+21620000001": LINA, "+21620000002": OMAR},
        )
        self.h = build(
            prospects=list(prospects),
            mode=ApprovalMode.AUTONOMOUS,
            brain=brain or ScriptedBrain(),
            telegram_consent_attribute=COLUMN,
        )
        limits = SendLimits(InMemorySendCapStore(), InMemorySendCounter(), self.h.clock)
        guard = limits.fixed_guard("client-a", "telegram_new", daily)
        self.limited = False
        self.channel = FirstContactCapChannel(
            TelegramAccountChannel(
                self.account,
                clock=self.h.clock,
                limited=lambda: self.limited,
                on_limited=lambda: setattr(self, "limited", True),
            ),
            guard,
        )
        self.h.channel = self.channel
        self.h.engine = self.h.new_engine()
        self.cursors = SqlMailCursorStore(sql_engine, "alpha")

    @property
    def engine(self):
        return self.h.engine

    def poll(self):
        inbox = TelegramAccountInbox(self.account, ACCOUNT_ID, clock=self.h.clock, link_limit=5)
        inbox.use_cursor(self.cursors)
        try:
            return poll_inbox(self.engine, inbox)
        finally:
            inbox.close()

    def cycle(self):
        return self.engine.run_cycle()

    def sent_to(self, peer):
        return [text for who, text in self.account.sent if who == peer]

    def stored(self, pid):
        """What the person wrote, as stored (not what was answered)."""
        messages = self.h.repo.get_conversation(pid).messages
        return [m for m in messages if m.direction is Direction.INBOUND]


@pytest.fixture
def room_of(sql_engine):
    return lambda *prospects, **kw: Room(sql_engine, *prospects, **kw)


def start(room):
    """The first poll only notes where the account stands."""
    assert room.poll().received == 0


class TestWritingFirst:
    def test_a_marked_prospect_is_found_by_their_number_and_written_to_from_the_clients_account(
        self, room_of
    ):
        room = room_of(person("lina", "+21620000001"))
        start(room)
        room.poll()  # finds the account
        assert room.h.prospect("lina").contact.telegram_chat_id == LINA
        assert room.account.lookups == ["+21620000001"]
        room.cycle()
        assert len(room.sent_to(LINA)) == 1
        assert room.h.prospect("lina").status is ProspectStatus.ACTIVE

    def test_an_unmarked_prospect_is_never_looked_up_nor_written_to(self, room_of):
        room = room_of(person("omar", "+21620000002", consent=None))
        start(room)
        room.poll()
        room.cycle()
        assert room.account.lookups == [] and room.account.sent == []
        assert [p.prospect_id for p in room.engine.prospects_awaiting_opt_in()] == ["omar"]
        assert room.h.prospect("omar").status is not ProspectStatus.PAUSED

    def test_a_number_telegram_does_not_know_waits_without_being_lost(self, room_of):
        room = room_of(person("ghost", "+21699999999"))
        start(room)
        room.poll()
        room.cycle()
        assert room.account.sent == []
        assert room.h.prospect("ghost").status is ProspectStatus.NEW

    def test_only_the_daily_maximum_of_new_conversations_goes_out_the_rest_is_deferred(
        self, room_of
    ):
        room = room_of(person("lina", "+21620000001"), person("omar", "+21620000002"), daily=1)
        start(room)
        room.poll()
        room.cycle()
        assert len(room.account.sent) == 1
        second = [a for a in room.h.repo.list_actions() if a.status is not ActionStatus.SENT]
        # not failed: the action stays approved with the reason, and goes out on a later day
        assert [a.status for a in second] == [ActionStatus.APPROVED]
        room.h.clock.advance(timedelta(days=1, hours=1))
        room.poll()
        room.cycle()
        assert len(room.account.sent) == 2

    def test_after_a_telegram_limit_no_first_message_leaves_again(self, room_of):
        room = room_of(person("lina", "+21620000001"), person("omar", "+21620000002"))
        start(room)
        room.poll()
        room.account.fail("send_text", "peer_flood")
        room.cycle()
        assert room.limited and room.account.sent == []
        room.h.clock.advance(timedelta(days=1))
        room.cycle()
        assert room.account.sent == []  # still stopped: only the Admin lifts it


class TestReading:
    def test_a_prospect_who_wrote_first_is_linked_stored_and_answered_without_any_mark(
        self, room_of
    ):
        room = room_of(person("omar", "+21620000002", consent=None))
        start(room)
        room.account.receive(OMAR, "Hello, I saw your offer", message_id=10, phone="+21620000002")
        report = room.poll()
        assert report.received == 1 and report.errors == ()
        assert room.h.prospect("omar").contact.telegram_chat_id == OMAR
        (message,) = room.stored("omar")
        assert message.text == "Hello, I saw your offer" and message.external_id == f"{OMAR}:10"
        room.cycle()
        assert len(room.sent_to(OMAR)) == 1  # the answer; it took no place in the daily maximum

    def test_an_answer_does_not_use_the_new_conversation_maximum(self, room_of):
        room = room_of(person("omar", "+21620000002", consent=None), daily=1)
        start(room)
        room.account.receive(OMAR, "Hi", message_id=11, phone="+21620000002")
        room.poll()
        room.cycle()
        room.account.receive(OMAR, "And the price?", message_id=12)
        room.poll()
        room.cycle()
        assert len(room.sent_to(OMAR)) == 2

    def test_a_person_who_is_not_a_prospect_is_never_read_or_stored(self, room_of):
        room = room_of(person("lina", "+21620000001", account=LINA))
        start(room)
        room.account.receive(STRANGER, "my private plans for the weekend", message_id=20)
        room.account.receive(LINA, "Interested", message_id=21)
        report = room.poll()
        assert report.received == 1
        stored = [m.text for p in room.h.repo.list_prospects() for m in room.stored(p.prospect_id)]
        assert stored == ["Interested"]
        assert len(room.h.repo.list_prospects()) == 1  # nobody was created from the stranger

    def test_nothing_written_before_the_connection_is_read(self, room_of):
        room = room_of(person("lina", "+21620000001", account=LINA))
        room.account.receive(LINA, "written long before", message_id=5)
        start(room)
        assert room.stored("lina") == []

    def test_a_restart_neither_loses_nor_repeats_a_message(self, room_of, sql_engine):
        room = room_of(person("lina", "+21620000001", account=LINA))
        start(room)
        room.account.receive(LINA, "one", message_id=30)
        assert room.poll().received == 1
        room.account.receive(LINA, "two", message_id=31)
        # a new process: a new inbox over the same database, the same Engine state
        room.cursors = SqlMailCursorStore(sql_engine, "alpha")
        assert room.poll().received == 1
        assert room.poll().received == 0
        assert [m.text for m in room.stored("lina")] == ["one", "two"]

    def test_stop_is_honoured_for_good(self, room_of):
        room = room_of(person("lina", "+21620000001", account=LINA))
        start(room)
        room.account.receive(LINA, "STOP", message_id=40)
        room.poll()
        room.cycle()
        assert room.h.prospect("lina").status is ProspectStatus.OPTED_OUT
        assert room.account.sent == []
        room.h.clock.advance(timedelta(days=3))
        room.cycle()
        assert room.account.sent == []

    def test_a_session_telegram_ended_stops_the_poll_and_is_reported(self, room_of):
        room = room_of(person("lina", "+21620000001", account=LINA))
        start(room)
        room.account.fail("fetch_incoming", "auth_lost")
        with pytest.raises(Exception, match="auth_lost"):
            room.poll()

    def test_two_clients_with_two_accounts_do_not_share_a_position(self, room_of, sql_engine):
        first = room_of(person("lina", "+21620000001", account=LINA))
        start(first)
        second = room_of(person("lina", "+21620000001", account=LINA))
        second.cursors = SqlMailCursorStore(sql_engine, "beta")
        start(second)
        first.account.receive(LINA, "for the first client", message_id=50)
        assert first.poll().received == 1
        assert second.poll().received == 0


def test_the_brain_is_asked_with_the_channel_of_the_clients_account(room_of):
    asked = []

    def brain(request):
        asked.append(request.prospect.prospect_id)
        return send("Hello from the client's own account")

    room = room_of(person("lina", "+21620000001", account=LINA), brain=ScriptedBrain(brain))
    start(room)
    room.cycle()
    assert asked == ["lina"]
    assert room.sent_to(LINA) == ["Hello from the client's own account"]
    assert Channel.TELEGRAM is room.channel.channel and NOW is not None
