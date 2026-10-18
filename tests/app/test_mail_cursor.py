"""Reading a person's own mailbox (T2): the SQL position, migration 0010, the scheduler's part, and
what reaches the Engine and its audit trail.

Covers: the migration (one table, nothing else, on top of 0009); the SQL store (per client and
mailbox, only forward, a fixed sentence when the database is down, concurrent writers); a poll that
gives the reader its place and refuses to read when there is none; the merged inbox; and, end to
end with a real Engine, that in `observe` mode a stranger's message never reaches the Engine, the
store or the audit trail, while `consume` (the historical way) still hands it over.

Everything is a double: SQLite in memory or in a temporary file, a double of the mail server. Nothing
here proves that a real server (or a PostgreSQL database) behaves the same way.
"""

import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import pytest
from engine_harness import build, prospect
from mail_server_double import NOW, ImapServerDouble, raw_mail
from sqlalchemy import inspect

from sales_app.bootstrap import _mail_cursor_stores
from sales_app.inbox import poll_inbox
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.mail_cursor_rows import SqlMailCursorStore
from sales_app.persistence.schema import current_revision
from sales_app.persistence.tables import metadata
from sales_app.scheduler import Scheduler
from sales_app.sourcing.errors import StoreUnavailableError
from sales_app.wiring.integrations import _MergedInbox
from sales_core.contracts import Channel, ContactInfo, ProspectId
from sales_integrations.mail import (
    ImapMailbox,
    ImapSettings,
    InMemoryMailCursorStore,
    MailCursor,
    MailCursorUnavailableError,
    mailbox_digest,
)

OWN = "sales@example.org"
KEY = mailbox_digest("imap.example.org", OWN, "INBOX")
PRIVATE_TEXT = "my private plans for the weekend"


class _Clock:
    def now(self):
        return NOW


def mailbox(server, read_mode="observe"):
    return ImapMailbox(
        ImapSettings(
            host="imap.example.org",
            port=993,
            security="ssl",
            username=OWN,
            password="S3cret-imap-pass!",
            own_addresses=(OWN,),
            read_mode=read_mode,
        ),
        factory=lambda settings: server,
        clock=_Clock(),
    )


@pytest.fixture
def engine():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    yield engine
    engine.dispose()


# ---- migration 0010 -----------------------------------------------------------------------------


class TestMigration0010:
    def test_it_adds_the_position_table_and_nothing_else_on_top_of_0009(self):
        engine = create_database_engine("sqlite://")
        upgrade_database(engine, "0009")
        before = set(inspect(engine).get_table_names())
        assert "op_mail_cursor" not in before
        upgrade_database(engine, "0010")
        assert current_revision(engine) == "0010"
        after = set(inspect(engine).get_table_names())
        assert after - before == {"op_mail_cursor"}
        # 0011 (the send cap), 0012 (payments) and 0013 (daily reports) come after: modelled, not here yet.
        later = {
            "client_send_caps",
            "op_send_counter",
            "payment_events",
            "daily_reports",
            "client_report_settings",
        }
        assert after - {"alembic_version"} == set(metadata.tables) - later

    def test_the_table_holds_a_position_and_nothing_of_any_message(self, engine):
        columns = {c["name"]: c for c in inspect(engine).get_columns("op_mail_cursor")}
        assert set(columns) == {
            "client_id",
            "mailbox_key",
            "uid_validity",
            "next_uid",
            "updated_at",
        }
        assert inspect(engine).get_pk_constraint("op_mail_cursor")["constrained_columns"] == [
            "client_id",
            "mailbox_key",
        ]

    def test_it_follows_0009_and_the_earlier_revisions_are_untouched_in_the_chain(self):
        from alembic.script import ScriptDirectory

        from sales_app.persistence.schema import _config

        script = ScriptDirectory.from_config(_config())
        chain = [
            script.get_revision(r).down_revision for r in ("0010", "0009", "0008", "0007", "0006")
        ]
        assert chain == ["0009", "0008", "0007", "0006", "0005"]


# ---- the SQL store ------------------------------------------------------------------------------


def cursor(next_uid, uidvalidity=7, at=NOW):
    return MailCursor(uidvalidity, next_uid, at)


def put(store, position, key=KEY):
    """The port trades plain values: a position goes in as its three numbers and date."""
    store.save(key, position.uidvalidity, position.next_uid, position.updated_at)


def got(store, key=KEY):
    held = store.load(key)
    return None if held is None else MailCursor(*held)


class TestTheSqlStore:
    def test_a_position_is_saved_and_read_back(self, engine):
        store = SqlMailCursorStore(engine, "alpha")
        assert got(store) is None
        put(store, cursor(5))
        assert got(store) == cursor(5)

    def test_a_client_never_sees_another_clients_position(self, engine):
        put(SqlMailCursorStore(engine, "alpha"), cursor(5))
        assert got(SqlMailCursorStore(engine, "beta")) is None
        put(SqlMailCursorStore(engine, "beta"), cursor(9))
        assert got(SqlMailCursorStore(engine, "alpha")) == cursor(5)

    def test_two_mailboxes_of_one_client_have_two_positions(self, engine):
        store = SqlMailCursorStore(engine, "alpha")
        other = mailbox_digest("imap.example.org", "other@example.org", "INBOX")
        put(store, cursor(5))
        put(store, cursor(2), other)
        assert (got(store).next_uid, got(store, other).next_uid) == (5, 2)

    def test_a_position_only_moves_forward(self, engine):
        store = SqlMailCursorStore(engine, "alpha")
        put(store, cursor(10))
        put(store, cursor(4))  # an older writer, arriving late
        put(store, cursor(10, at=datetime(2030, 1, 1, tzinfo=UTC)))  # not further on
        assert got(store) == cursor(10)
        put(store, cursor(11))
        assert got(store).next_uid == 11

    def test_a_new_uidvalidity_replaces_the_position_even_when_it_is_lower(self, engine):
        store = SqlMailCursorStore(engine, "alpha")
        put(store, cursor(500, uidvalidity=7))
        put(store, cursor(3, uidvalidity=8))
        assert got(store) == cursor(3, uidvalidity=8)

    def test_it_registers_the_client_it_is_bound_to(self, engine):
        put(SqlMailCursorStore(engine, "gamma"), cursor(1))
        assert got(SqlMailCursorStore(engine, "gamma")) is not None

    def test_a_database_that_is_down_is_a_fixed_sentence(self):
        broken = create_database_engine("sqlite://")  # never upgraded: there is no table
        store = SqlMailCursorStore(broken, "alpha")
        for call in (lambda: got(store), lambda: put(store, cursor(1))):
            with pytest.raises(StoreUnavailableError, match="unavailable") as error:
                call()
            assert "alpha" not in str(error.value) and "op_mail_cursor" not in str(error.value)

    def test_it_needs_a_client(self, engine):
        with pytest.raises(ValueError):
            SqlMailCursorStore(engine, "")

    def test_writers_arriving_in_any_order_leave_the_furthest_position(self, tmp_path):
        engine = create_database_engine(f"sqlite:///{tmp_path / 'positions.db'}")
        upgrade_database(engine)
        positions = list(range(1, 41))
        gate = threading.Barrier(8)
        written: list[int] = []

        def write(chunk):
            store = SqlMailCursorStore(engine, "alpha")
            gate.wait()
            for n in chunk:
                try:
                    put(store, cursor(n))
                except StoreUnavailableError:  # SQLite locking under contention
                    continue
                written.append(n)  # this one went through, or a further one already stood

        threads = [threading.Thread(target=write, args=(positions[i::8][::-1],)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        # Each write that went through either set the position or found a further one: so the
        # position is exactly the furthest of them, whatever the order they arrived in.
        assert written
        store = SqlMailCursorStore(engine, "alpha")
        assert got(store).next_uid == max(written)
        put(store, cursor(1))
        assert got(store).next_uid == max(written)
        engine.dispose()


class TestTheDeploymentsStores:
    def test_without_a_database_there_is_no_position_store(self):
        assert _mail_cursor_stores(None) is None
        assert _mail_cursor_stores(SimpleNamespace(engine=None)) is None

    def test_with_a_database_there_is_one_per_client(self, engine):
        factory = _mail_cursor_stores(SimpleNamespace(engine=engine))
        assert isinstance(factory("alpha"), SqlMailCursorStore)
        put(factory("alpha"), cursor(3))
        assert got(factory("beta")) is None and got(factory("alpha")) == cursor(3)


# ---- the scheduler gives the reader its place ---------------------------------------------------


class Inbox:
    def __init__(self, poller):
        self.poller, self.config_seen = poller, None

    def open_inbox(self, config):
        self.config_seen = config
        return self.poller

    def polls_inbox(self, config):
        return True


def scheduler(poller, mail_cursors=None):
    return Scheduler(
        SimpleNamespace(),
        _Clock(),
        executor=object(),
        inbox=Inbox(poller),
        mail_cursors=mail_cursors,
    )


RUNTIME = SimpleNamespace(config=SimpleNamespace(client_id="alpha"))


def email_prospect(pid="p1", address="alex@example.com"):
    return prospect(
        pid,
        contact=ContactInfo(phone="+15550000001", email=address, preferred_channel=Channel.EMAIL),
    )


class TestTheSchedulersPart:
    def test_a_reader_that_needs_a_place_gets_its_clients_own(self, engine):
        server = ImapServerDouble()
        server.deliver(raw_mail("alex@example.com", "old", message_id="old@mail.x"))
        h = build(prospects=[email_prospect()])
        asked: list[str] = []

        def cursors(client_id):
            asked.append(client_id)
            return SqlMailCursorStore(engine, client_id)

        run = scheduler(mailbox(server), cursors)
        assert run._poll(RUNTIME, h.engine).received == 0  # the first read: the end, no history
        server.deliver(raw_mail("alex@example.com", "new", message_id="new@mail.x"))
        assert run._poll(RUNTIME, h.engine).received == 1
        assert asked == ["alpha", "alpha"]
        assert got(SqlMailCursorStore(engine, "alpha")).next_uid == server.next_uid
        assert got(SqlMailCursorStore(engine, "beta")) is None

    def test_a_deployment_with_no_database_does_not_read_at_all(self):
        server = ImapServerDouble()
        server.deliver(raw_mail("alex@example.com", "x", message_id="x@mail.x"))
        h = build(prospects=[email_prospect()])
        with pytest.raises(MailCursorUnavailableError):
            scheduler(mailbox(server), None)._poll(RUNTIME, h.engine)
        assert server.commands("FETCH") == [] and server.logouts >= 1  # closed, nothing read
        assert h.repo.messages == ()

    def test_a_poller_that_needs_nothing_is_given_nothing(self):
        class Plain:
            def __init__(self):
                self.fetched = []

            def fetch(self, limit=50):
                self.fetched.append(limit)
                return []

            def acknowledge(self, handle):
                raise AssertionError

            def close(self):
                pass

            def use_cursor(self, store):
                raise AssertionError("a poller that does not ask for a place is not given one")

        plain = Plain()
        h = build()
        assert (
            scheduler(plain, lambda client_id: InMemoryMailCursorStore())
            ._poll(RUNTIME, h.engine)
            .received
            == 0
        )
        assert plain.fetched == [50]

    def test_a_merged_inbox_forwards_the_place_and_the_question_to_those_that_want_them(self):
        server = ImapServerDouble()
        server.deliver(raw_mail("alex@example.com", "old", message_id="old@mail.x"))

        class Plain:
            def __init__(self):
                self.calls = []

            def fetch(self, limit=50):
                self.calls.append(limit)
                return []

            def acknowledge(self, handle):
                pass

            def close(self):
                pass

        plain = Plain()
        merged = _MergedInbox([plain, mailbox(server)])
        assert merged.needs_cursor and merged.filters_by_sender
        store = InMemoryMailCursorStore()
        merged.use_cursor(store)
        h = build(prospects=[email_prospect()])
        assert poll_inbox(h.engine, merged).received == 0  # the first read of the observed one
        assert plain.calls == [50] and got(store) is not None
        assert (
            not _MergedInbox([plain]).needs_cursor and not _MergedInbox([plain]).filters_by_sender
        )


# ---- what reaches the Engine and its audit trail ------------------------------------------------


def private_mailbox() -> ImapServerDouble:
    server = ImapServerDouble()
    server.deliver(
        raw_mail(
            "friend@private.example", "history that is none of our business", message_id="h@mail.x"
        )
    )
    return server


def deliver_the_day(server):
    server.deliver(raw_mail("alex@example.com", "Yes, tell me more.", message_id="a@mail.x"))
    server.deliver(
        raw_mail("friend@private.example", PRIVATE_TEXT, message_id="f@mail.x", subject="Saturday")
    )
    server.deliver(
        raw_mail("bank@bigbank.example", "your statement and balance", message_id="b@mail.x")
    )
    server.deliver(
        raw_mail(
            "noreply@shop.example",
            "sale!",
            message_id="n@mail.x",
            extra={"Auto-Submitted": "auto-generated"},
        )
    )


def everything_written(h) -> str:
    return " ".join(repr(e) for e in h.repo.audit_events) + " ".join(
        repr(m) for m in h.repo.messages
    )


class TestWhatReachesTheEngine:
    def test_in_observe_mode_only_the_prospects_reply_reaches_the_engine_the_store_and_the_audit(
        self,
    ):
        server, store = private_mailbox(), InMemoryMailCursorStore()
        h = build(prospects=[email_prospect()])
        reader = mailbox(server)
        reader.use_cursor(store)
        assert poll_inbox(h.engine, reader).received == 0  # the first read: the end
        deliver_the_day(server)
        reader = mailbox(server)
        reader.use_cursor(store)
        try:
            report = poll_inbox(h.engine, reader)
        finally:
            reader.close()
        assert report.received == 1 and report.errors == ()
        (stored,) = h.repo.get_conversation(ProspectId("p1")).messages
        assert stored.text == "Yes, tell me more." and stored.external_id == "a@mail.x"
        written = everything_written(h)
        for private in (PRIVATE_TEXT, "friend@private.example", "bigbank", "Saturday", "statement"):
            assert private not in written, private
        assert "inbound.unknown_sender" not in h.event_types()
        assert server.commands("STORE") == []
        assert [server.messages[uid]["seen"] for uid in server.messages] == [False] * 5

    def test_the_text_of_anyone_elses_mail_never_leaves_the_server(self):
        server, store = private_mailbox(), InMemoryMailCursorStore()
        h = build(prospects=[email_prospect()])
        reader = mailbox(server)
        reader.use_cursor(store)
        poll_inbox(h.engine, reader)
        deliver_the_day(server)
        reader = mailbox(server)
        reader.use_cursor(store)
        poll_inbox(h.engine, reader)
        assert server.bodies_served == [2]  # the prospect's reply, and only it

    def test_a_prospect_with_an_unusual_case_in_its_address_is_still_heard(self):
        server, store = ImapServerDouble(), InMemoryMailCursorStore()
        h = build(prospects=[email_prospect(address="Alex@Example.COM")])
        reader = mailbox(server)
        reader.use_cursor(store)
        poll_inbox(h.engine, reader)
        server.deliver(raw_mail("alex@example.com", "hello", message_id="a@mail.x"))
        reader = mailbox(server)
        reader.use_cursor(store)
        assert poll_inbox(h.engine, reader).received == 1

    def test_a_reply_offered_again_after_a_position_was_lost_is_stored_once(self):
        class Switch:
            """A position store that can be taken down, as a database can."""

            def __init__(self, inner):
                self.inner, self.down = inner, False

            def load(self, mailbox):
                return self.inner.load(mailbox)

            def save(self, mailbox, uidvalidity, next_uid, updated_at):
                if self.down:
                    raise RuntimeError("down")
                self.inner.save(mailbox, uidvalidity, next_uid, updated_at)

        server, store = ImapServerDouble(), Switch(InMemoryMailCursorStore())
        h = build(prospects=[email_prospect()])

        def run():
            reader = mailbox(server)
            reader.use_cursor(store)
            try:
                return poll_inbox(h.engine, reader).received
            finally:
                reader.close()

        assert run() == 0  # the first read
        server.deliver(raw_mail("alex@example.com", "hello", message_id="same@mail.x"))
        store.down = True  # the Engine stores the reply, but the position cannot be kept
        assert run() == 1
        store.down = False
        assert run() == 1  # offered again...
        assert len(h.repo.get_conversation(ProspectId("p1")).messages) == 1  # ...stored once
        assert run() == 0

    def test_in_consume_mode_a_strangers_message_still_reaches_the_audit_as_it_always_did(self):
        # The contrast that shows what `observe` is for: the historical way hands everything over.
        server = ImapServerDouble()
        server.deliver(raw_mail("alex@example.com", "Yes, tell me more.", message_id="a@mail.x"))
        server.deliver(raw_mail("friend@private.example", PRIVATE_TEXT, message_id="f@mail.x"))
        h = build(prospects=[email_prospect()])
        reader = mailbox(server, "consume")
        try:
            report = poll_inbox(h.engine, reader)
        finally:
            reader.close()
        assert report.received == 2
        assert "inbound.unknown_sender" in h.event_types()
        assert PRIVATE_TEXT in everything_written(h)
        assert [c[3] for c in server.commands("STORE")] == ["+FLAGS", "+FLAGS"]
