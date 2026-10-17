"""Reading a mailbox without consuming it (`observe`), against a double of the server.

What is proved: nothing is ever written to the mailbox (no `STORE`, the folder is opened read-only,
every fetch is a peek); the body of a message is read only for a sender the caller accepts, and
only the headers that say who wrote are read for anyone else; the first read starts at the end, so
no history is touched; the position moves only past messages that are dealt with, survives a
restart, and falls back to a date when the server renumbers the folder; and that nothing is lost
when a read, a store or the position itself fails. Also that `consume`, the historical way, is
exactly what it was.

What is not: that a real server answers these commands the way the double does.
"""

from datetime import UTC, datetime, timedelta

import pytest
from mail_server_double import NOW, ImapServerDouble, raw_mail

from sales_core.contracts import Channel
from sales_integrations.mail import (
    ImapMailbox,
    ImapSettings,
    InMemoryMailCursorStore,
    MailCursor,
    MailCursorUnavailableError,
    MailUnavailableError,
    mailbox_digest,
)

PROSPECTS = {"alex@example.com", "bea@example.com", "cleo@example.com"}
OWN = "sales@example.org"


def settings(read_mode="observe", **kw):
    return ImapSettings(
        host="imap.example.org",
        port=993,
        security="ssl",
        username=OWN,
        password="S3cret-imap-pass!",
        own_addresses=(OWN,),
        read_mode=read_mode,
        **kw,
    )


def accept(channel, sender):
    assert channel is Channel.EMAIL
    return sender in PROSPECTS


class _Clock:
    def now(self):
        return NOW


def reader(server, store, **kw):
    mailbox = ImapMailbox(settings(**kw), factory=lambda s: server, clock=_Clock())
    mailbox.use_cursor(store)
    return mailbox


def reply(sender="alex@example.com", text="Yes, tell me more.", message_id="r1@mail.x", **kw):
    return raw_mail(sender, text, message_id=message_id, **kw)


def poll(server, store, *, acknowledge=True, limit=50):
    """One run: open, fetch, acknowledge what was stored, close. Returns the senders delivered."""
    mailbox = reader(server, store)
    try:
        items = mailbox.fetch(limit, accept=accept)
        if acknowledge:
            for item in items:
                mailbox.acknowledge(item.handle)
        return [i.message.sender for i in items]
    finally:
        mailbox.close()


def position(store, key):
    """What the store holds for a mailbox, as a `MailCursor` (the port trades plain values)."""
    held = store.load(key)
    return None if held is None else MailCursor(*held)


@pytest.fixture
def server():
    s = ImapServerDouble()
    s.deliver(reply(message_id="old1@mail.x"))  # history: here before anyone looked
    s.deliver(reply("friend@private.example", "my private plans", message_id="old2@mail.x"))
    return s


@pytest.fixture
def store():
    return InMemoryMailCursorStore()


def start(server, store):
    """The first read: it sets the position at the end and delivers nothing."""
    assert poll(server, store) == []


# ---- nothing is written -------------------------------------------------------------------------


class TestNothingIsWritten:
    def test_a_full_run_issues_no_store_opens_read_only_and_only_peeks(self, server, store):
        start(server, store)
        server.deliver(reply("alex@example.com", "first", message_id="a@mail.x"))
        server.deliver(reply("stranger@elsewhere.example", "buy now", message_id="s@mail.x"))
        server.deliver(
            reply(
                "noreply@shop.example",
                "sale!",
                message_id="n@mail.x",
                extra={"Auto-Submitted": "auto-generated"},
            )
        )
        server.deliver(reply("bea@example.com", "second", message_id="b@mail.x"))
        assert poll(server, store) == ["alex@example.com", "bea@example.com"]
        assert server.commands("STORE") == []
        assert {entry[2] for entry in server.log if entry[0] == "SELECT"} == {True}
        assert not any(seen for seen in server.seen_flags().values())

    def test_nothing_is_marked_read_even_for_a_message_that_was_stored(self, server, store):
        start(server, store)
        uid = server.deliver(reply())
        assert poll(server, store) == ["alex@example.com"]
        assert server.messages[uid]["seen"] is False and server.commands("STORE") == []

    def test_a_message_the_owner_already_read_is_still_delivered(self, server, store):
        start(server, store)
        server.deliver(reply(), seen=True)  # read in a mail client before Revansa came by
        assert poll(server, store) == ["alex@example.com"]


# ---- only the envelope of a stranger ------------------------------------------------------------


class TestOnlyWhatIsNeeded:
    def test_a_body_is_read_only_for_an_accepted_sender(self, server, store):
        start(server, store)
        prospect = server.deliver(reply("alex@example.com", "for you", message_id="a@mail.x"))
        stranger = server.deliver(
            reply("stranger@elsewhere.example", "secret", message_id="s@mail.x")
        )
        friend = server.deliver(reply("friend@private.example", "my diary", message_id="f@mail.x"))
        assert poll(server, store) == ["alex@example.com"]
        assert server.bodies_served == [prospect]
        assert stranger not in server.bodies_served and friend not in server.bodies_served
        assert sorted(server.headers_served) == [prospect, stranger, friend]

    def test_the_headers_asked_for_are_those_that_say_who_wrote_and_never_the_subject(
        self, server, store
    ):
        start(server, store)
        server.deliver(reply("stranger@elsewhere.example", "x", message_id="s@mail.x"))
        poll(server, store)
        specs = [entry[3] for entry in server.commands("FETCH")]
        assert specs and all("HEADER.FIELDS" in spec for spec in specs)
        for spec in specs:
            asked = set(spec.split("(")[2].rstrip(")]").split())
            assert asked == {
                "FROM",
                "SENDER",
                "AUTO-SUBMITTED",
                "PRECEDENCE",
                "LIST-ID",
                "X-FAILED-RECIPIENTS",
            }

    def test_the_mailboxs_own_and_automatic_messages_are_screened_by_their_headers_alone(
        self, server, store
    ):
        start(server, store)
        own = server.deliver(reply(OWN, "my own words", message_id="o@mail.x"))
        bounce = server.deliver(reply("mailer-daemon@x.example", "failed", message_id="m@mail.x"))
        listing = server.deliver(
            reply("list@x.example", "news", message_id="l@mail.x", extra={"List-Id": "<a.b>"})
        )
        assert poll(server, store) == []
        assert server.bodies_served == []
        assert {own, bounce, listing} <= set(server.headers_served)

    def test_every_fetch_is_a_peek(self, server, store):
        start(server, store)
        server.deliver(reply())
        poll(server, store)
        assert all("PEEK" in entry[3] for entry in server.commands("FETCH"))

    def test_an_accepted_sender_is_asked_about_by_address_in_lowercase(self, server, store):
        start(server, store)
        server.deliver(reply("Alex <ALEX@Example.COM>", "hi", message_id="c@mail.x"))
        assert poll(server, store) == ["alex@example.com"]

    def test_reading_without_saying_whom_to_accept_is_refused(self, server, store):
        mailbox = reader(server, store)
        with pytest.raises(ValueError):
            mailbox.fetch(10)
        assert server.commands("FETCH") == []


# ---- where it starts, and where it is ------------------------------------------------------------


class TestThePosition:
    def test_the_first_read_starts_at_the_end_and_touches_no_history(self, server, store):
        assert poll(server, store) == []
        assert server.commands("FETCH") == []
        key = mailbox_digest("imap.example.org", OWN, "INBOX")
        saved = position(store, key)
        assert (saved.uidvalidity, saved.next_uid) == (7, server.next_uid)

    def test_the_position_is_kept_under_a_digest_not_an_address(self, server, store):
        start(server, store)
        key = mailbox_digest("imap.example.org", OWN, "INBOX")
        assert OWN not in key and "example" not in key and len(key) == 64
        assert position(store, key) is not None

    def test_new_mail_after_the_first_read_is_delivered_once(self, server, store):
        start(server, store)
        server.deliver(reply(message_id="new@mail.x"))
        assert poll(server, store) == ["alex@example.com"]
        assert poll(server, store) == []

    def test_with_nothing_new_the_last_message_the_server_repeats_is_ignored(self, server, store):
        start(server, store)  # position past the last message: `n:*` answers with the last one
        before = position(store, mailbox_digest("imap.example.org", OWN, "INBOX"))
        assert poll(server, store) == []
        assert server.commands("FETCH") == []
        assert position(store, mailbox_digest("imap.example.org", OWN, "INBOX")) == before

    def test_a_restart_goes_on_from_the_saved_position(self, server, store):
        start(server, store)
        server.deliver(reply(message_id="one@mail.x"))
        assert poll(server, store) == ["alex@example.com"]
        server.deliver(reply("bea@example.com", "two", message_id="two@mail.x"))
        fresh = reader(server, store)  # a new process: only the store is shared
        try:
            items = fresh.fetch(50, accept=accept)
            assert [i.message.sender for i in items] == ["bea@example.com"]
        finally:
            fresh.close()

    def test_a_message_not_acknowledged_is_offered_again_after_a_restart(self, server, store):
        start(server, store)
        server.deliver(reply(message_id="one@mail.x"))
        assert poll(server, store, acknowledge=False) == ["alex@example.com"]
        assert poll(server, store) == ["alex@example.com"]
        assert poll(server, store) == []

    def test_a_failure_holds_the_position_back_whatever_follows_it(self, server, store):
        start(server, store)
        a = server.deliver(reply("alex@example.com", "a", message_id="a@mail.x"))
        b = server.deliver(reply("bea@example.com", "b", message_id="b@mail.x"))
        c = server.deliver(reply("cleo@example.com", "c", message_id="c@mail.x"))
        mailbox = reader(server, store)
        items = mailbox.fetch(50, accept=accept)
        by_uid = {int(i.handle): i for i in items}
        mailbox.acknowledge(str(a))
        mailbox.acknowledge(str(c))  # b failed to be stored: not acknowledged
        mailbox.close()
        saved = position(store, mailbox_digest("imap.example.org", OWN, "INBOX"))
        assert saved.next_uid == b and set(by_uid) == {a, b, c}
        assert poll(server, store) == ["bea@example.com", "cleo@example.com"]  # c again: harmless

    def test_senders_that_are_not_prospects_do_not_hold_the_position_back(self, server, store):
        start(server, store)
        server.deliver(reply("stranger@elsewhere.example", "x", message_id="s1@mail.x"))
        keep = server.deliver(reply("alex@example.com", "y", message_id="a@mail.x"))
        server.deliver(reply("stranger2@elsewhere.example", "z", message_id="s2@mail.x"))
        assert poll(server, store) == ["alex@example.com"]
        saved = position(store, mailbox_digest("imap.example.org", OWN, "INBOX"))
        assert saved.next_uid == keep + 2 == server.next_uid
        assert poll(server, store) == []

    def test_a_batch_is_taken_in_order_and_the_rest_is_left_for_the_next_run(self, server, store):
        start(server, store)
        for n in range(5):
            server.deliver(reply("alex@example.com", f"m{n}", message_id=f"m{n}@mail.x"))
        taken = []
        for _ in range(3):
            taken.append(poll(server, store, limit=2))
        assert [len(t) for t in taken] == [2, 2, 1]
        assert poll(server, store) == []

    def test_the_position_never_moves_backwards(self, server, store):
        start(server, store)
        key = mailbox_digest("imap.example.org", OWN, "INBOX")
        held = position(store, key)
        store.save(key, 7, 1, NOW)  # an older writer
        assert position(store, key) == held
        store.save(key, 7, held.next_uid + 5, NOW)
        assert position(store, key).next_uid == held.next_uid + 5

    def test_a_connection_check_reads_nothing_and_keeps_no_position(self, server, store):
        mailbox = reader(server, store)
        assert mailbox.fetch(0) == []
        mailbox.close()
        assert server.commands("SEARCH") == [] and server.commands("FETCH") == []
        assert position(store, mailbox_digest("imap.example.org", OWN, "INBOX")) is None
        assert server.logins == [(OWN, "S3cret-imap-pass!")]

    def test_a_server_that_does_not_announce_its_next_uid_is_asked_for_the_last_one(self, store):
        server = ImapServerDouble(announce_uidnext=False)
        server.deliver(reply(message_id="old@mail.x"))
        assert poll(server, store) == []
        assert position(store, mailbox_digest("imap.example.org", OWN, "INBOX")).next_uid == 2
        server.deliver(reply(message_id="new@mail.x"))
        assert poll(server, store) == ["alex@example.com"]

    def test_two_mailboxes_have_two_positions(self, store):
        one, other = ImapServerDouble(), ImapServerDouble()
        one.deliver(reply(message_id="x@mail.x"))
        assert poll(one, store) == []
        mailbox = ImapMailbox(
            ImapSettings(
                host="imap.example.org",
                port=993,
                security="ssl",
                username="other@example.org",
                password="p",
                read_mode="observe",
            ),
            factory=lambda s: other,
            clock=_Clock(),
        )
        mailbox.use_cursor(store)
        assert mailbox.fetch(10, accept=accept) == []
        mailbox.close()
        assert mailbox.cursor_key != mailbox_digest("imap.example.org", OWN, "INBOX")


# ---- the server renumbers the folder ------------------------------------------------------------


class TestUidValidity:
    def test_a_new_uidvalidity_reads_again_from_the_last_date_and_loses_nothing(self, store):
        server = ImapServerDouble(uidvalidity=7)
        server.deliver(
            reply("alex@example.com", "ancient", message_id="a0@mail.x"),
            date=NOW - timedelta(days=30),
        )
        assert poll(server, store) == []
        key = mailbox_digest("imap.example.org", OWN, "INBOX")
        server.deliver(reply("alex@example.com", "recent", message_id="a1@mail.x"), date=NOW)
        server.renumber(8)  # the server rebuilt the folder; the position kept means nothing now
        mailbox = reader(server, store)
        try:
            items = mailbox.fetch(50, accept=accept)
            assert [i.message.text for i in items] == [
                "recent"
            ]  # the ancient one is before the date
            assert mailbox.revalidated is True
            for item in items:
                mailbox.acknowledge(item.handle)
        finally:
            mailbox.close()
        saved = position(store, key)
        assert saved.uidvalidity == 8 and saved.next_uid == server.next_uid
        assert poll(server, store) == []

    def test_a_renumbered_folder_with_nothing_recent_still_records_the_new_uidvalidity(self, store):
        server = ImapServerDouble(uidvalidity=7)
        server.deliver(reply(message_id="a0@mail.x"), date=NOW - timedelta(days=90))
        poll(server, store)
        server.renumber(9)
        assert poll(server, store) == []
        saved = position(store, mailbox_digest("imap.example.org", OWN, "INBOX"))
        assert (saved.uidvalidity, saved.next_uid) == (9, server.next_uid)

    def test_the_old_numbers_are_never_trusted_in_a_new_uidvalidity(self, store):
        server = ImapServerDouble(uidvalidity=7)
        for n in range(5):
            server.deliver(reply("alex@example.com", f"m{n}", message_id=f"m{n}@mail.x"))
        poll(server, store)  # position: 6
        server.renumber(8)  # five messages again, numbered 1..5: all below the old position
        assert (
            poll(server, store) == ["alex@example.com"] * 5
        )  # read again; the Engine de-duplicates

    def test_a_server_that_gives_no_uidvalidity_is_not_read(self, store):
        server = ImapServerDouble()
        server.select = lambda mailbox="INBOX", readonly=False: ("OK", [b"0"])  # announces nothing
        with pytest.raises(MailUnavailableError):
            reader(server, store).fetch(10, accept=accept)
        assert server.commands("FETCH") == []


# ---- nothing is lost when something fails -------------------------------------------------------


class TestNothingIsLost:
    def test_a_read_that_fails_midway_loses_nothing(self, server, store):
        start(server, store)
        a = server.deliver(reply("alex@example.com", "a", message_id="a@mail.x"))
        b = server.deliver(reply("bea@example.com", "b", message_id="b@mail.x"))
        server.fail_fetch_of = b
        mailbox = reader(server, store)
        with pytest.raises(MailUnavailableError):
            mailbox.fetch(50, accept=accept)
        # a was fetched but never handed over: the position has not moved past it
        assert position(store, mailbox_digest("imap.example.org", OWN, "INBOX")).next_uid == a
        server.fail_fetch_of = None
        assert poll(server, store) == ["alex@example.com", "bea@example.com"]

    def test_a_stranger_read_before_the_failure_is_not_read_twice_for_nothing_it_is_just_skipped(
        self, server, store
    ):
        start(server, store)
        server.deliver(reply("stranger@elsewhere.example", "s", message_id="s@mail.x"))
        b = server.deliver(reply("bea@example.com", "b", message_id="b@mail.x"))
        server.fail_fetch_of = b
        with pytest.raises(MailUnavailableError):
            reader(server, store).fetch(50, accept=accept)
        saved = position(store, mailbox_digest("imap.example.org", OWN, "INBOX"))
        assert saved.next_uid == b  # the stranger before it is dealt with, the prospect is not
        server.fail_fetch_of = None
        assert poll(server, store) == ["bea@example.com"]

    def test_a_position_that_cannot_be_read_stops_the_read_before_anything_is_fetched(self, server):
        class Broken:
            def load(self, mailbox):
                raise RuntimeError("down")

            def save(self, mailbox, uidvalidity, next_uid, updated_at):
                raise RuntimeError("down")

        mailbox = reader(server, Broken())
        with pytest.raises(MailCursorUnavailableError):
            mailbox.fetch(10, accept=accept)
        assert server.commands("FETCH") == [] and server.commands("SEARCH") == []

    def test_no_place_to_keep_the_position_means_no_read(self, server):
        mailbox = reader(server, None)
        with pytest.raises(MailCursorUnavailableError):
            mailbox.fetch(10, accept=accept)
        assert server.commands("FETCH") == []

    class Flaky:
        """A position store that can be taken down and brought back."""

        def __init__(self, inner):
            self.inner, self.down = inner, False

        def load(self, mailbox):
            return self.inner.load(mailbox)

        def save(self, mailbox, uidvalidity, next_uid, updated_at):
            if self.down:
                raise RuntimeError("down")
            self.inner.save(mailbox, uidvalidity, next_uid, updated_at)

    def test_a_position_that_cannot_be_saved_costs_a_repeat_never_a_message(self, server, store):
        start(server, store)
        uid = server.deliver(reply(message_id="x@mail.x"))
        flaky = self.Flaky(store)
        mailbox = reader(server, flaky)
        items = mailbox.fetch(50, accept=accept)
        flaky.down = True
        mailbox.acknowledge(items[0].handle)  # stored; the position cannot be kept: no error here
        mailbox.close()
        assert position(store, mailbox_digest("imap.example.org", OWN, "INBOX")).next_uid == uid
        assert poll(server, store) == [
            "alex@example.com"
        ]  # offered again; the Engine de-duplicates
        assert poll(server, store) == []

    def test_closing_keeps_the_position_that_could_not_be_kept_before(self, server, store):
        start(server, store)
        uid = server.deliver(reply(message_id="x@mail.x"))
        flaky = self.Flaky(store)
        mailbox = reader(server, flaky)
        items = mailbox.fetch(50, accept=accept)
        flaky.down = True
        mailbox.acknowledge(items[0].handle)
        flaky.down = False  # the database is back
        mailbox.close()
        assert position(store, mailbox_digest("imap.example.org", OWN, "INBOX")).next_uid == uid + 1
        assert poll(server, store) == []


# ---- the historical way is exactly what it was --------------------------------------------------


class TestConsumeIsUnchanged:
    def consume(self, server):
        return ImapMailbox(settings("consume"), factory=lambda s: server, clock=_Clock())

    def test_it_searches_unseen_opens_for_writing_and_marks_read_after_the_acknowledgement(self):
        server = ImapServerDouble()
        server.deliver(reply(message_id="a@mail.x"))
        server.deliver(reply("stranger@elsewhere.example", "s", message_id="s@mail.x"))
        server.deliver(reply("noreply@shop.example", "sale", message_id="n@mail.x"))
        mailbox = self.consume(server)
        assert mailbox.needs_cursor is False and mailbox.filters_by_sender is False
        items = mailbox.fetch(50)
        assert [i.message.sender for i in items] == [
            "alex@example.com",
            "stranger@elsewhere.example",
        ]
        assert server.commands("SEARCH") == [("UID", "SEARCH", None, "UNSEEN")]
        assert [entry for entry in server.log if entry[0] == "SELECT"] == [
            ("SELECT", "INBOX", False)
        ]
        # the automatic one is marked read and dropped at once; the others only once acknowledged
        assert server.seen_flags() == {1: False, 2: False, 3: True}
        for item in items:
            mailbox.acknowledge(item.handle)
        mailbox.close()
        assert server.seen_flags() == {1: True, 2: True, 3: True}
        assert [c[3] for c in server.commands("STORE")] == ["+FLAGS"] * 3

    def test_it_reads_whole_messages_and_never_asks_for_numbering_or_a_position(self):
        server = ImapServerDouble()
        server.deliver(
            reply("stranger@elsewhere.example", "a stranger's words", message_id="s@mail.x")
        )
        mailbox = self.consume(server)
        items = mailbox.fetch(50)
        assert [i.message.text for i in items] == ["a stranger's words"]
        assert server.bodies_served == [1] and server.headers_served == []
        assert server.responses_asked == []  # the numbering was never read
        mailbox.close()

    def test_it_needs_no_position_store_and_ignores_one(self):
        server = ImapServerDouble()
        server.deliver(reply(message_id="a@mail.x"))
        store = InMemoryMailCursorStore()
        mailbox = self.consume(server)
        mailbox.use_cursor(store)
        assert [i.message.sender for i in mailbox.fetch(50, accept=lambda c, s: False)] == [
            "alex@example.com"
        ]
        mailbox.close()
        assert position(store, mailbox_digest("imap.example.org", OWN, "INBOX")) is None

    def test_a_consume_mailbox_that_has_no_numbering_support_still_reads(self):
        class Plain(ImapServerDouble):
            response = None  # a connection with no `response` at all, like the older doubles

        server = Plain()
        server.deliver(reply(message_id="a@mail.x"))
        assert [i.message.sender for i in self.consume(server).fetch(50)] == ["alex@example.com"]

    def test_the_date_helper_is_locale_free(self):
        from sales_integrations.mail.mailbox import _imap_date

        assert _imap_date(datetime(2026, 3, 4, 23, 30, tzinfo=UTC)) == "04-Mar-2026"
