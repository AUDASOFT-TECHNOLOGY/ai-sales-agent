"""The daily send cap applies to EVERY e-mail of a client, in both e-mail modes.

The application builds a client's channel for the Engine in one place (`wiring.runtime._metered`):
a metered channel (e-mail) goes behind `SendCapChannel` there, whichever adapter it is (a dedicated
mailbox, a Gmail mailbox). These tests prove, with doubles only (no network, no real send):

* nothing reaches SMTP without a cap, over the cap, or when the store cannot be read;
* the effective cap is the Admin's cap, per client, per UTC day: the plan's quota never lowers it;
* the plan's daily quota counts new commercial contacts only (never follow-ups, reminders, replies);
* a certain failure gives the place back, an ambiguous one (the channel raised) does not;
* the counter and the cap survive a restart (SQL), and clients never share them;
* without a guard (no database) a metered channel sends nothing; a non-metered one is untouched.

The Engine side (a refused action stays approved, with its reason, without using its attempts) and
the real application wiring are proved end to end in `test_commercial_journey_e2e.py`.
"""

import smtplib
from datetime import UTC, datetime, timedelta

import pytest
from app_support import make_settings
from test_google_oauth_flow import ADDRESS, Tokens
from test_google_oauth_flow import FakeSmtp as GoogleSmtp

from sales_app.google_mailbox import CapEnforcedByWiring, OAuthMailChannel
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.send_cap_rows import SqlSendCapStore, SqlSendCounter
from sales_app.send_cap import (
    CAP_NOT_CONFIGURED,
    CAP_REACHED,
    CAP_UNAVAILABLE,
    COMMERCIAL_CHANNEL,
    COMMERCIAL_QUOTA_REACHED,
    InMemorySendCapStore,
    InMemorySendCounter,
    InvalidSendCapError,
    RefusingChannel,
    SendCapChannel,
    SendLimits,
    combine_caps,
)
from sales_app.sourcing.errors import StoreUnavailableError
from sales_app.wiring import default_builders
from sales_app.wiring.integrations import ChannelProvider
from sales_app.wiring.runtime import _metered
from sales_core.contracts import Channel, MessageDraft, OutboundMessage, SendStatus
from sales_core.contracts.messaging import DEFERRED_KEY, DEFERRED_VALUE
from sales_integrations.fakes import FakeClock
from sales_integrations.mail import MailChannel, SmtpSettings

NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


class Smtp:
    """The wire: records what is handed to the server."""

    def __init__(self, fail=None):
        self.sent = []
        self.fail = fail

    def login(self, user, password):
        pass

    def send_message(self, message):
        if self.fail is not None:
            raise self.fail
        self.sent.append(message["To"])

    def quit(self):
        pass


def message(client="alpha", n=1, new_contact=False):
    return OutboundMessage(
        new_contact=new_contact,
        client_id=client,
        prospect_id=f"p{n}",
        channel=Channel.EMAIL,
        recipient=f"lead{n}@example.org",
        draft=MessageDraft(text="Hello"),
        idempotency_key=f"{client}-{n}",
    )


def dedicated(wire):
    settings = SmtpSettings(
        host="smtp.example.org",
        port=465,
        security="ssl",
        username="a@example.org",
        password="x",
        from_address="a@example.org",
        from_name="A",
    )
    return MailChannel(settings, factory=lambda s: wire, clock=FakeClock(NOW))


def gmail(log):
    return OAuthMailChannel(
        Tokens("t1", "t2", "t3", "t4"),
        ADDRESS,
        clock=FakeClock(NOW),
        smtp_factory=lambda s: GoogleSmtp(log),
        send_guard=CapEnforcedByWiring(),  # the wiring counts; the adapter must not count twice
    )


def provider(build, *, metered=True):
    return ChannelProvider(
        kind=Channel.EMAIL,
        build=build,
        routing_key=lambda c: None,
        webhook_secret=lambda c: None,
        build_parser=None,
        metered=metered,
    )


class Config:
    def __init__(self, client_id):
        self.client_id = client_id


class World:
    """Stores, counter and limits over doubles, wired like the application's builders."""

    def __init__(self, *, caps=None, counter=None):
        self.clock = FakeClock(NOW)
        self.caps = caps or InMemorySendCapStore()
        self.counter = counter or InMemorySendCounter()
        self.plan_quotas: dict[str, int] = {}
        self.limits = SendLimits(
            self.caps, self.counter, self.clock, lambda client: self.plan_quotas.get(client)
        )

    def channel(self, client, build, *, metered=True, guards=True, commercial=False):
        class Builders:  # the attributes `_metered` reads
            send_guards = self.limits.guard if guards else None
            quota_channel = self.limits.commercial_channel if commercial else None
            send_allowed = None

        return _metered(provider(build, metered=metered), Config(client), Builders)

    def cap(self, client, value):
        self.caps.set(client, value, by="admin:root", at=NOW)


# ---- the effective cap ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("admin", "plan", "effective"),
    [
        (10, 3, 10),  # the plan's quota of new contacts does not lower the e-mail cap
        (2, 200, 2),
        (5, 5, 5),
        (4, None, 4),  # no plan: the Admin's cap alone
        (None, 200, None),  # a plan is not a cap: no Admin cap, nothing is sent
        (None, None, None),
        (0, 5, None),
        (-1, 5, None),
        (True, 5, None),
        ("5", 5, None),
        (5, 0, 5),
        (5, "9", 5),
        (5, True, 5),
    ],
)
def test_the_effective_cap_is_the_admin_cap_and_the_plan_quota_never_lowers_it(
    admin, plan, effective
):
    assert combine_caps(admin, plan).effective == effective


def test_the_cap_has_a_ceiling_and_a_floor():
    store = InMemorySendCapStore()
    for bad in (0, -1, 100_001, True, 2.5, "3"):
        with pytest.raises(InvalidSendCapError):
            store.set("alpha", bad, by="admin:root", at=NOW)
    store.set("alpha", 100_000, by="admin:root", at=NOW)
    assert store.get("alpha") == 100_000


# ---- nothing is sent without a place ------------------------------------------------------------


def test_no_cap_nothing_reaches_smtp_in_either_mode():
    w = World()
    wire, log = Smtp(), []
    for channel in (
        w.channel("alpha", lambda c: dedicated(wire)),
        w.channel("alpha", lambda c: gmail(log)),
    ):
        refused = channel.send(message())
        assert not refused.ok and refused.error_code == CAP_NOT_CONFIGURED
        assert refused.status is SendStatus.FAILED_TRANSIENT and refused.error_message
        assert refused.metadata[DEFERRED_KEY] == DEFERRED_VALUE
    assert wire.sent == [] and log == []
    assert w.counter.used("alpha", "email", NOW.date()) == 0


def test_the_cap_is_reached_then_refused_in_both_modes_with_one_shared_count():
    w = World()
    w.cap("alpha", 3)
    wire, log = Smtp(), []
    dedicated_channel = w.channel("alpha", lambda c: dedicated(wire))
    gmail_channel = w.channel("alpha", lambda c: gmail(log))
    assert dedicated_channel.send(message(n=1)).ok
    assert gmail_channel.send(message(n=2)).ok
    assert dedicated_channel.send(message(n=3)).ok
    for channel in (dedicated_channel, gmail_channel):  # the same counter: the fourth is refused
        assert channel.send(message(n=4)).error_code == CAP_REACHED
    assert len(wire.sent) == 2 and log.count(("send",)) == 1
    assert w.counter.used("alpha", "email", NOW.date()) == 3  # counted once per message
    w.clock.advance(timedelta(days=1))  # a new UTC day
    assert gmail_channel.send(message(n=5)).ok


def test_the_plan_quota_counts_new_contacts_only_and_follows_a_plan_change():
    w = World()
    w.plan_quotas["alpha"] = 2
    w.cap("alpha", 50)
    wire = Smtp()
    channel = w.channel("alpha", lambda c: dedicated(wire), commercial=True)
    day = NOW.date()
    assert channel.send(message(n=1, new_contact=True)).ok
    assert channel.send(message(n=2, new_contact=True)).ok
    refused = channel.send(message(n=3, new_contact=True))
    assert refused.error_code == COMMERCIAL_QUOTA_REACHED
    assert refused.metadata[DEFERRED_KEY] == DEFERRED_VALUE
    # follow-ups, reminders and replies (not new contacts) pass and are not counted in the quota
    assert channel.send(message(n=4)).ok and channel.send(message(n=5)).ok
    assert w.counter.used("alpha", COMMERCIAL_CHANNEL, day) == 2
    assert w.counter.used("alpha", "email", day) == 4  # the e-mail cap still counts every message
    w.plan_quotas["alpha"] = 3  # a bigger plan is applied at once
    assert channel.send(message(n=3, new_contact=True)).ok
    del w.plan_quotas["alpha"]  # off any plan: no quota of new contacts, the Admin's cap alone
    assert channel.send(message(n=6, new_contact=True)).ok
    assert w.counter.used("alpha", COMMERCIAL_CHANNEL, day) == 3
    w.clock.advance(timedelta(days=1))  # a new UTC day starts again
    w.plan_quotas["alpha"] = 1
    assert channel.send(message(n=7, new_contact=True)).ok
    assert channel.send(message(n=8, new_contact=True)).error_code == COMMERCIAL_QUOTA_REACHED


def test_a_new_contact_that_fails_gives_its_place_back_and_a_refused_one_costs_nothing():
    w = World()
    w.plan_quotas["alpha"] = 5
    w.cap("alpha", 1)
    day = NOW.date()
    refusing = Smtp(fail=smtplib.SMTPRecipientsRefused({"x": (550, b"no")}))
    assert not w.channel("alpha", lambda c: dedicated(refusing), commercial=True).send(
        message(n=1, new_contact=True)
    ).ok
    assert w.counter.used("alpha", COMMERCIAL_CHANNEL, day) == 0
    channel = w.channel("alpha", lambda c: dedicated(Smtp()), commercial=True)
    assert channel.send(message(n=2, new_contact=True)).ok  # takes the only e-mail place too
    # refused by the e-mail cap: the commercial place it took is given back
    assert channel.send(message(n=3, new_contact=True)).error_code == CAP_REACHED
    assert w.counter.used("alpha", COMMERCIAL_CHANNEL, day) == 1


def test_concurrent_new_contacts_never_exceed_the_quota(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    engine = create_database_engine(f"sqlite:///{tmp_path / 'race.db'}")
    upgrade_database(engine)
    try:
        w = World(caps=SqlSendCapStore(engine), counter=SqlSendCounter(engine))
        w.plan_quotas["alpha"] = 5
        w.cap("alpha", 100)
        channel = w.channel("alpha", lambda c: dedicated(Smtp()), commercial=True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(lambda n: channel.send(message(n=n, new_contact=True)), range(20))
            )
        assert sum(r.ok for r in results) == 5
        assert w.counter.used("alpha", COMMERCIAL_CHANNEL, NOW.date()) == 5
    finally:
        engine.dispose()


def test_a_plan_that_cannot_be_read_refuses_new_contacts_only():
    w = World()
    w.cap("alpha", 10)

    def broken(client):
        raise StoreUnavailableError("plans are unavailable")

    w.limits = SendLimits(w.caps, w.counter, w.clock, broken)
    wire = Smtp()
    channel = w.channel("alpha", lambda c: dedicated(wire), commercial=True)
    refused = channel.send(message(new_contact=True))
    assert refused.error_code == CAP_UNAVAILABLE and wire.sent == []
    assert channel.send(message(n=2)).ok  # a reply does not need the plan


def test_a_store_that_cannot_be_read_refuses():
    class Down(InMemorySendCounter):
        def try_increment(self, *args):
            raise StoreUnavailableError("down")

    w = World(counter=Down())
    w.cap("alpha", 10)
    wire = Smtp()
    refused = w.channel("alpha", lambda c: dedicated(wire)).send(message())
    assert refused.error_code == CAP_UNAVAILABLE and wire.sent == []


def test_clients_never_share_a_cap_or_a_count():
    w = World()
    w.cap("alpha", 1)
    w.cap("beta", 1)
    a_wire, b_wire = Smtp(), Smtp()
    alpha = w.channel("alpha", lambda c: dedicated(a_wire))
    beta = w.channel("beta", lambda c: dedicated(b_wire))
    assert alpha.send(message("alpha")).ok
    assert alpha.send(message("alpha", 2)).error_code == CAP_REACHED
    assert beta.send(message("beta")).ok  # beta's place is its own
    gamma = w.channel("gamma", lambda c: dedicated(Smtp()))  # no cap of its own
    assert gamma.send(message("gamma")).error_code == CAP_NOT_CONFIGURED
    assert (len(a_wire.sent), len(b_wire.sent)) == (1, 1)


# ---- success, certain failure, ambiguous failure --------------------------------------------------


def test_a_certain_failure_gives_the_place_back_and_an_ambiguous_one_does_not():
    w = World()
    w.cap("alpha", 2)
    day = NOW.date()

    # the server refuses the recipient: it is certain nothing left, the place is returned
    refusing = Smtp(fail=smtplib.SMTPRecipientsRefused({"x": (550, b"no")}))
    assert not w.channel("alpha", lambda c: dedicated(refusing)).send(message()).ok
    assert w.counter.used("alpha", "email", day) == 0

    # the connection drops after the handoff: the message may have left, the place stays used
    dropped = Smtp(fail=smtplib.SMTPServerDisconnected("lost"))
    with pytest.raises(smtplib.SMTPServerDisconnected):
        w.channel("alpha", lambda c: dedicated(dropped)).send(message())
    assert w.counter.used("alpha", "email", day) == 1

    # the Gmail adapter obeys the same rule (it used to hand the place back when it raised)
    class Raising(GoogleSmtp):
        def send_message(self, m):
            raise TimeoutError("slow")

    adapter = OAuthMailChannel(
        Tokens("t"),
        ADDRESS,
        clock=FakeClock(NOW),
        smtp_factory=lambda s: Raising([]),
        send_guard=CapEnforcedByWiring(),
    )
    with pytest.raises(TimeoutError):
        w.channel("alpha", lambda c: adapter).send(message())
    assert w.counter.used("alpha", "email", day) == 2
    last = w.channel("alpha", lambda c: dedicated(Smtp())).send(message())
    assert last.error_code == CAP_REACHED


# ---- the wiring ------------------------------------------------------------------------------------


def test_without_a_guard_a_metered_channel_sends_nothing_and_others_are_untouched(tmp_path):
    w = World()
    w.cap("alpha", 5)
    wire = Smtp()
    built = w.channel("alpha", lambda c: dedicated(wire), guards=False)  # no database
    assert isinstance(built, RefusingChannel)
    refused = built.send(message())
    assert refused.error_code == CAP_UNAVAILABLE
    assert refused.metadata[DEFERRED_KEY] == DEFERRED_VALUE
    assert wire.sent == []
    # a channel that is not metered (a test double, another channel) is returned as it is
    plain = dedicated(wire)
    assert w.channel("alpha", lambda c: plain, metered=False, guards=False) is plain
    assert isinstance(w.channel("alpha", lambda c: dedicated(wire)), SendCapChannel)

    # only e-mail is metered in the real provider table
    builders = default_builders(make_settings(tmp_path))
    assert builders.channels["email"].metered is True
    assert all(not p.metered for key, p in builders.channels.items() if key != "email")


# ---- persistence -----------------------------------------------------------------------------------


def test_the_cap_and_the_count_survive_a_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'caps.db'}"
    engine = create_database_engine(url)
    upgrade_database(engine)
    w = World(caps=SqlSendCapStore(engine), counter=SqlSendCounter(engine))
    w.cap("alpha", 2)
    wire = Smtp()
    channel = w.channel("alpha", lambda c: dedicated(wire))
    assert channel.send(message(n=1)).ok and channel.send(message(n=2)).ok
    engine.dispose()

    again = create_database_engine(url)  # a new process on the same database
    try:
        w2 = World(caps=SqlSendCapStore(again), counter=SqlSendCounter(again))
        assert w2.caps.get("alpha") == 2
        assert w2.counter.used("alpha", "email", NOW.date()) == 2
        wire2 = Smtp()
        refused = w2.channel("alpha", lambda c: dedicated(wire2)).send(message(n=3))
        assert refused.error_code == CAP_REACHED and wire2.sent == []
        assert w2.caps.get("beta") is None  # nobody inherits a cap
    finally:
        again.dispose()
