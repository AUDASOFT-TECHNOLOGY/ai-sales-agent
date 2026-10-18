"""The client's daily report: built from stored data, kept once per client and day, sent honestly.

SIMULATED here: the messaging channels (`FakeMessagingChannel`, nothing leaves the machine) and the
clock. REAL here: the report builder, the service, the stores (the in-memory ones and the SQL ones on
a SQLite file, migrated with Alembic), the real e-mail send cap (`DailySendGuard` over a real
counter), the scheduler job and the Client API routes. Nothing proves PostgreSQL behaves the same.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from engine_harness import ScriptedBrain  # noqa: F401  (keeps the shared harness importable)
from factories import make_message, make_prospect
from test_client_agent_api import World

from sales_app.daily_report import (
    CHANNEL_NOT_AUTHORIZED,
    FAILED,
    PENDING,
    SEND_UNCONFIRMED,
    SENDING,
    SENT,
    SETTINGS_MISSING,
    UNDELIVERABLE,
    DailyReportService,
    InMemoryReportSettingsStore,
    InMemoryReportStore,
    InvalidReportSettingsError,
    ReportChannelUnavailableError,
    SendCapQuota,
    build_settings,
    latest_report_day,
    window_for,
)
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.daily_report_rows import SqlReportSettingsStore, SqlReportStore
from sales_app.persistence.send_cap_rows import SqlSendCapStore, SqlSendCounter
from sales_app.persistence.tables import metadata
from sales_app.scheduler import Job, Scheduler
from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore
from sales_app.send_cap import (
    DailySendGuard,
    InMemorySendCapStore,
    InMemorySendCounter,
)
from sales_core.contracts import (
    Channel,
    ConversationId,
    Direction,
    MessageId,
    ProspectId,
    ProspectStatus,
    SalesActionType,
    Scheduling,
    SendStatus,
)
from sales_integrations.fakes import FakeClock, FakeMessagingChannel

TUNIS = ZoneInfo("Africa/Tunis")  # UTC+1, no daylight saving
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)  # 13:00 in Tunis
DAY = date(2026, 10, 17)
WINDOW_START = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)  # 09:00 Tunis the day before
WINDOW_END = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)  # 09:00 Tunis


# ---- doubles ------------------------------------------------------------------------------------


class Reader:
    """What the Engine offers the report: a client's prospects and conversations, read-only."""

    def __init__(self, repo):
        self._repo = repo

    def list_prospects(self):
        return self._repo.list_prospects()

    def conversation_of(self, prospect_id):
        return self._repo.get_conversation(prospect_id)


class Channels:
    """Opens the fake channel of a client, or fails like a channel that cannot be reached."""

    def __init__(self, clock):
        self.clock = clock
        self.fakes = {}
        self.unavailable = False
        self.raising = False

    def open(self, client_id, channel):
        if self.unavailable:
            raise ReportChannelUnavailableError()
        fake = self.fake(client_id, channel)
        if self.raising:
            return _Raising()
        return fake

    def fake(self, client_id, channel):
        return self.fakes.setdefault(
            (client_id, channel), FakeMessagingChannel(Channel(channel), clock=self.clock)
        )

    def sent(self, client_id, channel):
        return self.fakes[(client_id, channel)].sent if (client_id, channel) in self.fakes else ()


class _Raising:
    channel = Channel.EMAIL

    def send(self, message):
        raise RuntimeError("boom: smtp password hunter2")


@pytest.fixture(params=["sql", "memory"])
def stores(request, tmp_path):
    """(settings store, report store, send counter, cap store), SQL on a file or in memory."""
    if request.param == "memory":
        return (
            InMemoryReportSettingsStore(),
            InMemoryReportStore(),
            InMemorySendCounter(),
            InMemorySendCapStore(),
        )
    engine = create_database_engine(f"sqlite:///{tmp_path / 'reports.db'}")
    upgrade_database(engine)
    return (
        SqlReportSettingsStore(engine),
        SqlReportStore(engine),
        SqlSendCounter(engine),
        (SqlSendCapStore(engine)),
    )


class Rig:
    def __init__(self, stores, *, allowed=True, guard=True, cap=50):
        self.settings, self.reports, self.counter, self.caps = stores
        self.clock = FakeClock(NOW)
        self.channels = Channels(self.clock)
        self.cap = cap
        for client in ("alpha", "beta"):
            if cap is not None:
                self.caps.set(client, cap, by="admin:root", at=NOW)
        self.allowed = allowed
        self.guard = guard

    def service(self) -> DailyReportService:
        """A fresh service over the same stores: what a restart gives."""
        return DailyReportService(
            settings=self.settings,
            reports=self.reports,
            channels=self.channels,
            now=self.clock.now,
            quota=SendCapQuota(self.caps.get, self.counter.used),
            send_guard=(
                (
                    lambda cid: DailySendGuard(
                        cid, "email", lambda: self.caps.get(cid), self.clock, self.counter
                    )
                )
                if self.guard
                else None
            ),
            channel_allowed=lambda cid, channel: self.allowed,
            default_timezone=lambda cid: "Africa/Tunis",
        )

    def configure(self, cid="alpha", **over):
        values = {
            "enabled": True,
            "channel": "email",
            "recipient": "owner@example.com",
            "send_time": "09:00",
            "timezone": "Africa/Tunis",
            "language": "en",
        } | over
        settings = build_settings(cid, at=NOW, by=f"client:{cid}", **values)
        self.settings.set(settings)
        return settings


def repository(cid):
    from sales_core.contracts import ClientId
    from sales_integrations.fakes import FakeSalesRepository

    return FakeSalesRepository(ClientId(cid))


def say(repo, cid, pid, direction, at, n):
    repo.append_messages(
        [
            make_message(
                client_id=cid,
                prospect_id=ProspectId(pid),
                conversation_id=ConversationId(f"conv-{pid}"),
                message_id=MessageId(f"{cid}-{pid}-{n}"),
                direction=direction,
                channel=Channel.EMAIL,
                text=f"message {n}",
                timestamp=at,
            )
        ]
    )


def populate(repo, cid):
    """Three prospects. In the window (09:00 Tunis the day before to 09:00 today):
    p1: our first message, its reply, our answer, then a follow-up with no reply in between;
    p2: one message just before the window (not counted) and one inside (a follow-up);
    p3: paused (waits for a person), a follow-up scheduled in 5 hours."""
    repo.add_prospects(
        make_prospect(client_id=cid, prospect_id=ProspectId("p1"), display_name="Amel"),
        make_prospect(client_id=cid, prospect_id=ProspectId("p2"), display_name="Bilel"),
        make_prospect(
            client_id=cid,
            prospect_id=ProspectId("p3"),
            display_name="Chadi",
            status=ProspectStatus.PAUSED,
        ),
    )
    hour = timedelta(hours=1)
    say(repo, cid, "p1", Direction.OUTBOUND, WINDOW_START + hour, 1)
    say(repo, cid, "p1", Direction.INBOUND, WINDOW_START + 2 * hour, 2)
    say(repo, cid, "p1", Direction.OUTBOUND, WINDOW_START + 3 * hour, 3)
    say(repo, cid, "p1", Direction.OUTBOUND, WINDOW_START + 4 * hour, 4)
    say(repo, cid, "p2", Direction.OUTBOUND, WINDOW_START - timedelta(minutes=1), 1)  # before
    say(repo, cid, "p2", Direction.OUTBOUND, WINDOW_START + 5 * hour, 2)
    say(repo, cid, "p2", Direction.INBOUND, WINDOW_END + timedelta(minutes=1), 3)  # after


# ---- the figures ------------------------------------------------------------------------------


class TestFigures:
    def test_they_come_from_the_stored_data_inside_the_window_only(self, stores):
        rig = Rig(stores)
        rig.configure()
        repo = repository("alpha")
        populate(repo, "alpha")
        repo.update_prospects(
            [
                make_prospect(
                    client_id="alpha",
                    prospect_id=ProspectId("p2"),
                    display_name="Bilel",
                    schedule=Scheduling(
                        next_action_type=SalesActionType.FOLLOW_UP,
                        next_action_due_at=NOW + timedelta(hours=5),
                    ),
                )
            ]
        )
        report = rig.service().generate("alpha", Reader(repo), {"status": "ready"})
        figures = report.figures
        assert figures["activity"] == {
            "messages_sent": 4,  # p1 x3, p2 x1 (the one before the window is not counted)
            "prospects_contacted": 2,
            "new_contacts": 1,  # p1's first message
            "follow_ups_sent": 2,  # p1's 4th (after our own 3rd) and p2's second message
            "answers_sent": 1,  # p1's 3rd answers its reply
            "replies_received": 1,  # p2's reply came after the window
            "prospects_replied": 1,
        }
        assert figures["prospects"]["total"] == 3
        assert figures["prospects"]["by_status"]["paused"] == 1
        assert figures["follow_ups"] == {"scheduled": 1, "due_next_24h": 1}
        period = figures["period"]
        assert period["day"] == "2026-10-17" and period["timezone"] == "Africa/Tunis"
        assert report.window_start == WINDOW_START and report.window_end == WINDOW_END

    def test_what_is_not_tracked_is_said_so_and_no_sale_is_claimed(self, stores):
        rig = Rig(stores)
        rig.configure()
        repo = repository("alpha")
        populate(repo, "alpha")
        report = rig.service().generate("alpha", Reader(repo), None)
        for key in ("purchase_intents", "appointments", "conversions"):
            assert report.figures[key] == {"tracked": False}
        assert "not tracked" in report.text
        assert "not a confirmed sale" not in report.text.lower().replace("nothing here is a", "")
        assert "nothing here is a confirmed sale" in report.text

    def test_actions_required_are_derived_from_the_data(self, stores):
        rig = Rig(stores)
        rig.configure()
        repo = repository("alpha")
        populate(repo, "alpha")
        health = {"status": "degraded", "consecutive_failures": 2, "inbox_failures": 1}
        report = rig.service().generate("alpha", Reader(repo), health)
        codes = {item["code"] for item in report.figures["actions_required"]}
        assert codes == {"needs_human", "channel_degraded", "inbox_failing"}
        assert "last_error" not in report.figures["health"]  # counters only, never a message

    def test_the_email_quota_left_is_read_from_the_send_cap(self, stores):
        rig = Rig(stores, cap=10)
        rig.configure()
        rig.counter.try_increment("alpha", "email", DAY, 10)
        rig.counter.try_increment("alpha", "email", DAY, 10)
        report = rig.service().generate("alpha", Reader(repository("alpha")), None)
        assert report.figures["quota"] == {
            "channel": "email",
            "status": "ok",
            "day_utc": "2026-10-17",
            "cap": 10,
            "used": 2,
            "remaining": 8,
        }

    def test_no_cap_is_reported_as_not_configured(self, stores):
        rig = Rig(stores, cap=None)
        rig.configure()
        report = rig.service().generate("alpha", Reader(repository("alpha")), None)
        assert report.figures["quota"]["status"] == "not_configured"
        assert {"code": "send_cap_not_configured"} in report.figures["actions_required"]

    def test_the_text_follows_the_chosen_language(self, stores):
        rig = Rig(stores)
        rig.configure(language="fr")
        report = rig.service().generate("alpha", Reader(repository("alpha")), None)
        assert report.text.startswith("Rapport quotidien 2026-10-17")


# ---- time zones ---------------------------------------------------------------------------------


class TestTimeZones:
    def test_the_window_is_the_24_hours_ending_at_the_local_time(self):
        start, end = window_for(DAY, time(9, 0), TUNIS)
        assert (start, end) == (WINDOW_START, WINDOW_END)

    def test_across_a_daylight_saving_change_it_follows_the_wall_clock(self):
        paris = ZoneInfo("Europe/Paris")  # clocks go back on 2026-10-25
        start, end = window_for(date(2026, 10, 25), time(9, 0), paris)
        assert end - start == timedelta(hours=25)
        assert end == datetime(2026, 10, 25, 8, 0, tzinfo=UTC)

    def test_the_report_day_is_the_local_day_not_the_utc_day(self):
        zone = ZoneInfo("Pacific/Auckland")  # UTC+13 in October
        moment = datetime(2026, 10, 17, 23, 0, tzinfo=UTC)  # 12:00 on the 18th there
        assert latest_report_day(moment, time(9, 0), zone) == date(2026, 10, 18)
        assert latest_report_day(moment, time(9, 0), ZoneInfo("UTC")) == date(2026, 10, 17)

    def test_before_the_local_time_the_newest_report_is_yesterdays(self):
        early = datetime(2026, 10, 17, 7, 59, tzinfo=UTC)  # 08:59 in Tunis
        assert latest_report_day(early, time(9, 0), TUNIS) == date(2026, 10, 16)

    @pytest.mark.parametrize("zone", ["", " ", "Mars/Base", "Africa/Tunis ", "+01:00", None, 3])
    def test_the_zone_must_be_an_explicit_iana_name(self, zone):
        with pytest.raises(InvalidReportSettingsError) as error:
            build_settings(
                "alpha",
                enabled=True,
                channel="email",
                recipient="a@example.com",
                send_time="09:00",
                timezone=zone,
                language="en",
                at=NOW,
                by="t",
            )
        assert error.value.code == "invalid_timezone"

    @pytest.mark.parametrize("value", ["9:00", "24:00", "09:60", "", "nine", None])
    def test_the_time_must_be_a_valid_hour_and_minute(self, value):
        with pytest.raises(InvalidReportSettingsError) as error:
            build_settings(
                "alpha",
                enabled=True,
                channel="email",
                recipient="a@example.com",
                send_time=value,
                timezone="Africa/Tunis",
                language="en",
                at=NOW,
                by="t",
            )
        assert error.value.code == "invalid_time"


# ---- channels -----------------------------------------------------------------------------------


class TestChannels:
    @pytest.mark.parametrize("channel", ["whatsapp", "sms"])
    def test_whatsapp_is_not_a_supported_report_channel(self, channel):
        with pytest.raises(InvalidReportSettingsError) as error:
            build_settings(
                "alpha",
                enabled=True,
                channel=channel,
                recipient="+21620000001",
                send_time="09:00",
                timezone="Africa/Tunis",
                language="en",
                at=NOW,
                by="t",
            )
        assert error.value.code == "unsupported_channel"

    @pytest.mark.parametrize(
        ("channel", "recipient"),
        [
            ("email", "not an address"),
            ("email", "a@b.co\nBcc: x@y.zz"),
            ("email", "a@example.com, b@example.com"),
            ("telegram", "@username"),
            ("telegram", "12 34"),
        ],
    )
    def test_the_recipient_must_fit_the_channel(self, channel, recipient):
        with pytest.raises(InvalidReportSettingsError) as error:
            build_settings(
                "alpha",
                enabled=True,
                channel=channel,
                recipient=recipient,
                send_time="09:00",
                timezone="Africa/Tunis",
                language="en",
                at=NOW,
                by="t",
            )
        assert error.value.code == "invalid_recipient"

    def test_email_goes_to_the_clients_address_and_takes_a_place_in_the_cap(self, stores):
        rig = Rig(stores, cap=5)
        rig.configure()
        service = rig.service()
        report = service.generate("alpha", Reader(repository("alpha")), None)
        sent = service.deliver("alpha", report.day)
        assert sent.status == SENT and sent.sent_at == NOW and sent.error_code is None
        (message,) = rig.channels.sent("alpha", "email")
        assert message.recipient == "owner@example.com"
        assert message.draft.text == report.text
        assert rig.counter.used("alpha", "email", DAY) == 1
        assert sent.recipient_hint == "o***@example.com"

    def test_telegram_goes_to_the_chat_and_is_not_counted_in_the_email_cap(self, stores):
        rig = Rig(stores, cap=5)
        rig.configure(channel="telegram", recipient="123456789")
        service = rig.service()
        report = service.generate("alpha", Reader(repository("alpha")), None)
        assert service.deliver("alpha", report.day).status == SENT
        (message,) = rig.channels.sent("alpha", "telegram")
        assert message.recipient == "123456789" and message.channel is Channel.TELEGRAM
        assert rig.counter.used("alpha", "email", DAY) == 0

    def test_the_cap_reached_blocks_the_email_and_the_report_is_kept_retryable(self, stores):
        rig = Rig(stores, cap=1)
        rig.configure()
        rig.counter.try_increment("alpha", "email", DAY, 1)  # the day's only place is gone
        service = rig.service()
        report = service.generate("alpha", Reader(repository("alpha")), None)
        result = service.deliver("alpha", report.day)
        assert result.status == FAILED and result.error_code == "send_cap_reached"
        assert rig.channels.sent("alpha", "email") == ()
        assert result.sent_at is None
        assert result.attempts == 0  # a refusal is not an attempt
        assert result.next_attempt_at > NOW
        assert rig.counter.used("alpha", "email", DAY) == 1  # nothing was taken

    def test_the_cap_not_configured_blocks_the_email(self, stores):
        rig = Rig(stores, cap=None)
        rig.configure()
        service = rig.service()
        report = service.generate("alpha", Reader(repository("alpha")), None)
        result = service.deliver("alpha", report.day)
        assert result.status == FAILED and result.error_code == "send_cap_not_configured"
        assert rig.channels.sent("alpha", "email") == ()

    def test_no_guard_wired_means_no_email_at_all(self, stores):
        rig = Rig(stores, guard=False)
        rig.configure()
        service = rig.service()
        report = service.generate("alpha", Reader(repository("alpha")), None)
        assert service.deliver("alpha", report.day).error_code == "send_cap_unavailable"
        assert rig.channels.sent("alpha", "email") == ()

    def test_a_channel_the_admin_has_not_opened_makes_it_undeliverable(self, stores):
        rig = Rig(stores, allowed=False)
        rig.configure()
        service = rig.service()
        report = service.generate("alpha", Reader(repository("alpha")), None)
        result = service.deliver("alpha", report.day)
        assert result.status == UNDELIVERABLE and result.error_code == CHANNEL_NOT_AUTHORIZED
        assert rig.channels.fakes == {}


# ---- delivery is honest, idempotent and retryable ---------------------------------------------


class TestDelivery:
    def test_a_channel_that_is_down_leaves_the_report_failed_never_sent(self, stores):
        rig = Rig(stores)
        rig.configure()
        rig.channels.unavailable = True
        service = rig.service()
        report = service.generate("alpha", Reader(repository("alpha")), None)
        result = service.deliver("alpha", report.day)
        assert result.status == FAILED and result.error_code == "channel_unavailable"
        assert result.sent_at is None and result.attempts == 1
        assert result.next_attempt_at == NOW + timedelta(seconds=60)
        assert rig.reports.get("alpha", DAY).status == FAILED  # the report is kept

    def test_the_scheduler_run_retries_after_the_backoff_and_then_delivers_once(self, stores):
        rig = Rig(stores)
        rig.configure()
        rig.channels.unavailable = True
        repo = Reader(repository("alpha"))
        service = rig.service()
        first = service.run_scheduled("alpha", repo, None, NOW)
        assert not first.ok and first.errors == ("report 2026-10-17: channel_unavailable",)
        rig.channels.unavailable = False
        rig.clock.advance(timedelta(seconds=30))  # inside the backoff: not due
        assert service.due("alpha", rig.clock.now()) is False
        rig.clock.advance(timedelta(seconds=31))
        assert service.due("alpha", rig.clock.now()) is True
        assert service.run_scheduled("alpha", repo, None, rig.clock.now()).ok
        assert rig.reports.get("alpha", DAY).status == SENT
        assert len(rig.channels.sent("alpha", "email")) == 1
        assert service.due("alpha", rig.clock.now()) is False

    def test_after_the_attempts_run_out_it_stays_failed_for_the_client_to_resend(self, stores):
        rig = Rig(stores)
        rig.configure()
        rig.channels.unavailable = True
        repo = Reader(repository("alpha"))
        service = rig.service()
        for _ in range(12):
            service.run_scheduled("alpha", repo, None, rig.clock.now())
            rig.clock.advance(timedelta(hours=2))
        record = rig.reports.get("alpha", latest_report_day(NOW, time(9, 0), TUNIS))
        assert record.status == FAILED and record.attempts == 6
        rig.channels.unavailable = False
        assert service.deliver("alpha", record.day).status == SENT  # the client asked

    def test_one_report_per_client_and_day_whatever_the_restarts(self, stores):
        rig = Rig(stores)
        rig.configure()
        reader = Reader(repository("alpha"))
        a, b = rig.service(), rig.service()  # two processes, or a restart
        first = a.generate("alpha", reader, None)
        second = b.generate("alpha", reader, None)
        assert first.day == second.day and first.generated_at == second.generated_at
        assert len(rig.reports.list("alpha", 10)) == 1
        a.run_scheduled("alpha", reader, None, NOW)
        b.run_scheduled("alpha", reader, None, NOW)
        rig.service().run_scheduled("alpha", reader, None, NOW)
        assert len(rig.channels.sent("alpha", "email")) == 1
        assert rig.counter.used("alpha", "email", DAY) == 1

    def test_delivering_a_sent_report_again_sends_nothing(self, stores):
        rig = Rig(stores)
        rig.configure()
        service = rig.service()
        report = service.generate("alpha", Reader(repository("alpha")), None)
        service.deliver("alpha", report.day)
        again = service.deliver("alpha", report.day)
        assert again.status == SENT
        assert len(rig.channels.fakes[("alpha", "email")].attempts) == 1

    def test_the_report_of_a_past_day_is_never_regenerated_with_new_figures(self, stores):
        rig = Rig(stores)
        rig.configure()
        repo = repository("alpha")
        service = rig.service()
        first = service.generate("alpha", Reader(repo), None)
        populate(repo, "alpha")  # data arriving later does not rewrite a report that exists
        again = service.generate("alpha", Reader(repo), None)
        assert again.figures == first.figures
        assert again.figures["activity"]["messages_sent"] == 0

    def test_a_channel_that_raises_leaves_an_unconfirmed_send_that_is_not_retried_alone(
        self, stores
    ):
        rig = Rig(stores)
        rig.configure()
        rig.channels.raising = True
        repo = Reader(repository("alpha"))
        service = rig.service()
        service.run_scheduled("alpha", repo, None, NOW)
        record = rig.reports.get("alpha", DAY)
        assert record.status == FAILED and record.error_code == SEND_UNCONFIRMED
        assert "hunter2" not in repr(record) and "smtp" not in record.error_code
        rig.channels.raising = False
        rig.clock.advance(timedelta(hours=1))
        assert service.due("alpha", rig.clock.now()) is False
        service.run_scheduled("alpha", repo, None, rig.clock.now())
        assert rig.channels.sent("alpha", "email") == ()
        assert service.deliver("alpha", DAY).status == SENT  # only on the client's request

    def test_a_permanent_refusal_is_undeliverable_and_keeps_only_a_code(self, stores):
        rig = Rig(stores)
        rig.configure(channel="telegram", recipient="42")
        rig.channels.fake("alpha", "telegram").queue_results(
            _failure(SendStatus.FAILED_PERMANENT, "blocked_by_user 'secret-token-123'")
        )
        service = rig.service()
        report = service.generate("alpha", Reader(repository("alpha")), None)
        result = service.deliver("alpha", report.day)
        assert result.status == UNDELIVERABLE and result.error_code == "blocked_by_user"
        assert "secret-token" not in repr(result)
        assert result.next_attempt_at is None

    def test_a_send_left_in_progress_by_a_dead_process_is_settled_not_resent(self, stores):
        rig = Rig(stores)
        rig.configure()
        service = rig.service()
        report = service.generate("alpha", Reader(repository("alpha")), None)
        assert rig.reports.claim("alpha", report.day, (PENDING,), NOW) is not None  # then it died
        assert service.deliver("alpha", report.day).status == SENDING  # a live send: left alone
        later = NOW + timedelta(minutes=11)
        rig.clock.set(later)
        service.run_scheduled("alpha", Reader(repository("alpha")), None, later)
        record = rig.reports.get("alpha", DAY)
        assert record.status == FAILED and record.error_code == SEND_UNCONFIRMED
        assert rig.channels.sent("alpha", "email") == ()


def _failure(status, message):
    from sales_core.contracts import SendResult

    return SendResult(
        status=status, timestamp=NOW, error_code=message.split()[0], error_message=message
    )


# ---- no valid configuration -----------------------------------------------------------------------


class TestWithoutConfiguration:
    def test_a_report_is_kept_but_not_sent_and_the_problem_is_named(self, stores):
        rig = Rig(stores)
        service = rig.service()
        report = service.generate("alpha", Reader(repository("alpha")), None)
        assert report.status == UNDELIVERABLE and report.error_code == SETTINGS_MISSING
        assert service.deliver("alpha", report.day).status == UNDELIVERABLE
        assert rig.channels.fakes == {}
        assert rig.reports.get("alpha", DAY) is not None

    def test_the_scheduler_does_nothing_without_settings(self, stores):
        rig = Rig(stores)
        service = rig.service()
        assert service.due("alpha", NOW) is False
        assert service.run_scheduled("alpha", Reader(repository("alpha")), None, NOW).ok
        assert rig.reports.list("alpha", 10) == []

    def test_a_disabled_report_is_not_made_by_the_scheduler(self, stores):
        rig = Rig(stores)
        rig.configure(enabled=False)
        assert rig.service().due("alpha", NOW) is False

    def test_fixing_the_configuration_delivers_the_kept_report(self, stores):
        rig = Rig(stores)
        service = rig.service()
        service.generate("alpha", Reader(repository("alpha")), None)  # kept: no settings
        rig.configure()
        assert service.due("alpha", NOW) is True
        assert service.run_scheduled("alpha", Reader(repository("alpha")), None, NOW).ok
        assert rig.reports.get("alpha", DAY).status == SENT
        assert len(rig.channels.sent("alpha", "email")) == 1


# ---- isolation ------------------------------------------------------------------------------------


class TestIsolation:
    def test_each_client_gets_its_own_report_figures_address_and_cap(self, stores):
        rig = Rig(stores, cap=3)
        rig.configure("alpha", recipient="alpha@example.com")
        rig.configure("beta", recipient="beta@example.com")
        alpha_repo, beta_repo = repository("alpha"), repository("beta")
        populate(alpha_repo, "alpha")
        service = rig.service()
        a = service.generate("alpha", Reader(alpha_repo), None)
        b = service.generate("beta", Reader(beta_repo), None)
        assert a.figures["activity"]["messages_sent"] == 4
        assert b.figures["activity"]["messages_sent"] == 0
        assert b.figures["prospects"]["total"] == 0
        service.deliver("alpha", DAY)
        service.deliver("beta", DAY)
        assert rig.channels.sent("alpha", "email")[0].recipient == "alpha@example.com"
        assert rig.channels.sent("beta", "email")[0].recipient == "beta@example.com"
        assert rig.counter.used("alpha", "email", DAY) == 1
        assert rig.counter.used("beta", "email", DAY) == 1
        assert [r.client_id for r in rig.reports.list("alpha", 10)] == ["alpha"]
        assert rig.reports.get("beta", DAY).figures["prospects"]["total"] == 0

    def test_one_clients_exhausted_cap_does_not_block_the_other(self, stores):
        rig = Rig(stores, cap=1)
        rig.configure("alpha")
        rig.configure("beta")
        rig.counter.try_increment("alpha", "email", DAY, 1)
        service = rig.service()
        service.generate("alpha", Reader(repository("alpha")), None)
        service.generate("beta", Reader(repository("beta")), None)
        assert service.deliver("alpha", DAY).status == FAILED
        assert service.deliver("beta", DAY).status == SENT


# ---- the schema ----------------------------------------------------------------------------------


class TestSchema:
    def test_the_migration_creates_the_tables_and_a_second_report_for_a_day_is_refused(
        self, tmp_path
    ):
        engine = create_database_engine(f"sqlite:///{tmp_path / 'm.db'}")
        upgrade_database(engine)
        from sqlalchemy import inspect

        assert {"client_report_settings", "daily_reports"} <= set(inspect(engine).get_table_names())
        assert {"client_report_settings", "daily_reports"} <= set(metadata.tables)
        store = SqlReportStore(engine)
        rig = Rig(
            (SqlReportSettingsStore(engine), store, SqlSendCounter(engine), SqlSendCapStore(engine))
        )
        rig.configure()
        record = rig.service().generate("alpha", Reader(repository("alpha")), None)
        assert store.add(record) is False

    def test_the_chain_ends_at_0013_after_0012(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        from sales_app.persistence.schema import MIGRATIONS_DIR

        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        script = ScriptDirectory.from_config(config)
        assert script.get_revision("0013").down_revision == "0012"
        assert len(script.get_heads()) == 1

    def test_the_report_survives_a_new_engine(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'r.db'}"
        first = create_database_engine(url)
        upgrade_database(first)
        rig = Rig(
            (
                SqlReportSettingsStore(first),
                SqlReportStore(first),
                SqlSendCounter(first),
                SqlSendCapStore(first),
            )
        )
        rig.configure()
        rig.service().run_scheduled("alpha", Reader(repository("alpha")), None, NOW)
        second = create_database_engine(url)
        reopened = SqlReportStore(second).get("alpha", DAY)
        assert reopened.status == SENT and reopened.figures["period"]["day"] == "2026-10-17"
        assert SqlReportSettingsStore(second).get("alpha").timezone == "Africa/Tunis"


# ---- the scheduler job ---------------------------------------------------------------------------


class FakeReports:
    def __init__(self, due=True, boom=False):
        self.is_due = due
        self.boom = boom
        self.calls = []

    def due(self, client_id, now):
        if self.boom == "due":
            raise RuntimeError("db down")
        return self.is_due

    def run_scheduled(self, client_id, reader, health, now):
        self.calls.append((client_id, reader, now))
        if self.boom is True:
            raise RuntimeError("secret value")
        from sales_app.daily_report import ReportRun

        return ReportRun(ok=True)


class TestSchedulerJob:
    def build(self, tmp_path, reports):
        from app_support import InlineExecutor, SpyEngine, build_test_application

        app, _made, clock = build_test_application(tmp_path, "alpha")
        for runtime in app.registry.runtimes():
            runtime.engine = SpyEngine()
        scheduler = Scheduler(app.registry, clock, executor=InlineExecutor(), reports=reports)
        return app, scheduler, clock

    def run(self, scheduler, now):
        return [r for future in scheduler.tick(now) for r in future.result()]

    def test_the_report_job_runs_on_its_own_even_outside_the_outbound_window(self, tmp_path):
        reports = FakeReports()
        _app, scheduler, _ = self.build(tmp_path, reports)
        sunday_night = datetime(2026, 1, 18, 22, 0, tzinfo=UTC)
        jobs = [r.job for r in self.run(scheduler, sunday_night)]
        assert Job.REPORT in jobs and Job.DAILY not in jobs
        assert reports.calls[0][0] == "alpha"

    def test_it_is_looked_at_only_once_per_period(self, tmp_path):
        reports = FakeReports(due=False)
        _app, scheduler, _ = self.build(tmp_path, reports)
        now = datetime(2026, 1, 18, 22, 0, tzinfo=UTC)
        assert Job.REPORT not in [r.job for r in self.run(scheduler, now)]
        reports.is_due = True  # due again, but the store is not asked before the period ends
        assert Job.REPORT not in [r.job for r in self.run(scheduler, now + timedelta(minutes=2))]
        assert Job.REPORT in [r.job for r in self.run(scheduler, now + timedelta(minutes=6))]

    def test_a_failing_report_backs_off_without_stopping_the_other_jobs(self, tmp_path):
        reports = FakeReports(boom=True)
        app, scheduler, _ = self.build(tmp_path, reports)
        monday = datetime(2026, 1, 19, 9, 30, tzinfo=UTC)  # inside the outbound window
        results = self.run(scheduler, monday)
        by_job = {r.job: r for r in results}
        assert by_job[Job.REPORT].ok is False
        assert by_job[Job.REPORT].errors == ("report failed",)  # never what it raised
        assert by_job[Job.DAILY].ok is True
        state = app.registry.get("alpha").state
        assert state.report_blocked_until == monday + timedelta(seconds=60)
        assert app.registry.get("alpha").health.snapshot()["consecutive_failures"] == 0
        assert Job.REPORT not in [
            r.job for r in self.run(scheduler, monday + timedelta(seconds=30))
        ]

    def test_an_unreadable_store_means_not_due_not_a_crash(self, tmp_path):
        reports = FakeReports(boom="due")
        _app, scheduler, _ = self.build(tmp_path, reports)
        assert Job.REPORT not in [
            r.job for r in self.run(scheduler, datetime(2026, 1, 18, 22, 0, tzinfo=UTC))
        ]


# ---- the Client API ------------------------------------------------------------------------------

REPORTING = "/client/v1/reporting"
SETTINGS = "/client/v1/reporting/settings"
REPORTS = "/client/v1/reports"
BODY = {
    "enabled": True,
    "channel": "email",
    "recipient": "owner@example.com",
    "send_time": "09:00",
    "timezone": "Africa/Tunis",
    "language": "en",
}


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path, secret_store=InMemorySecretStore(), secret_audit=InMemorySecretAudit())
    engine = create_database_engine(f"sqlite:///{tmp_path / 'api.db'}")
    upgrade_database(engine)
    rig = Rig(
        (
            SqlReportSettingsStore(engine),
            SqlReportStore(engine),
            SqlSendCounter(engine),
            SqlSendCapStore(engine),
        )
    )
    rig.clock = w.clock
    rig.channels = Channels(w.clock)
    w.clock.set(NOW)
    w.rig = rig
    w.application.daily_reports = rig.service()
    # The Admin opens email to alpha only.
    w.application.channel_policy.set_client_access(
        "alpha",
        ["email", "telegram"],
        [],
        actor="admin:root",
        known=["whatsapp", "email", "telegram"],
    )
    yield w
    w.application.close()


class TestApi:
    def test_every_route_needs_a_client_session(self, world):
        anonymous = world.anonymous()
        assert anonymous.get(REPORTING).status_code == 401
        assert anonymous.put(SETTINGS, json=BODY).status_code == 401
        assert anonymous.get(REPORTS).status_code == 401
        assert anonymous.get(f"{REPORTS}/2026-10-17").status_code == 401
        assert anonymous.post(f"{REPORTS}/generate").status_code == 401
        assert anonymous.post(f"{REPORTS}/2026-10-17/resend").status_code == 401

    def test_a_new_client_sees_no_settings_and_the_channels_it_may_choose(self, world):
        response = world.browser("alpha").get(REPORTING)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["settings"] is None and body["latest"] is None
        channels = {c["key"]: c for c in body["channels"]}
        assert channels["email"]["available"] and channels["telegram"]["available"]
        assert channels["whatsapp"] == {
            "key": "whatsapp",
            "available": False,
            "reason": "not_supported",
        }
        beta = {c["key"]: c for c in world.browser("beta").get(REPORTING).json()["channels"]}
        assert beta["email"] == {"key": "email", "available": False, "reason": "not_authorized"}

    def test_settings_are_saved_and_read_back(self, world):
        alpha = world.browser("alpha")
        saved = alpha.put(SETTINGS, json=BODY)
        assert saved.status_code == 200, saved.text
        assert saved.json()["timezone"] == "Africa/Tunis" and saved.json()["send_time"] == "09:00"
        assert alpha.get(REPORTING).json()["settings"]["recipient"] == "owner@example.com"
        assert world.browser("beta").get(REPORTING).json()["settings"] is None

    @pytest.mark.parametrize(
        ("change", "code"),
        [
            ({"channel": "whatsapp"}, "unsupported_channel"),
            ({"channel": "fax"}, "unknown_channel"),
            ({"timezone": "Nowhere/Land"}, "invalid_timezone"),
            ({"timezone": ""}, "invalid_timezone"),
            ({"send_time": "25:00"}, "invalid_time"),
            ({"recipient": "nope"}, "invalid_recipient"),
            ({"language": "de"}, "invalid_language"),
        ],
    )
    def test_invalid_settings_are_refused_with_a_code(self, world, change, code):
        response = world.browser("alpha").put(SETTINGS, json={**BODY, **change})
        assert response.status_code == 422
        assert response.json()["detail"] == code
        assert world.application.daily_reports.settings_of("alpha") is None

    def test_an_unknown_field_is_refused_not_ignored(self, world):
        response = world.browser("alpha").put(SETTINGS, json={**BODY, "client_id": "beta"})
        assert response.status_code == 422

    def test_a_channel_the_admin_has_not_opened_cannot_be_chosen(self, world):
        response = world.browser("beta").put(SETTINGS, json=BODY)
        assert response.status_code == 403
        assert world.application.daily_reports.settings_of("beta") is None

    def test_generating_makes_one_report_sends_it_once_and_says_so(self, world):
        world.rig.caps.set("alpha", 5, by="admin:root", at=NOW)
        alpha = world.browser("alpha")
        alpha.put(SETTINGS, json=BODY)
        first = alpha.post(f"{REPORTS}/generate")
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["status"] == "sent" and body["day"] == "2026-10-17"
        assert body["figures"]["purchase_intents"] == {"tracked": False}
        assert body["text"].startswith("Daily report 2026-10-17")
        second = alpha.post(f"{REPORTS}/generate").json()
        assert second["generated_at"] == body["generated_at"] and second["status"] == "sent"
        assert len(world.rig.channels.sent("alpha", "email")) == 1
        listed = alpha.get(REPORTS).json()["reports"]
        assert [r["status"] for r in listed] == ["sent"]
        assert alpha.get(REPORTING).json()["latest"]["day"] == "2026-10-17"

    def test_without_settings_the_report_is_kept_and_says_nothing_was_sent(self, world):
        alpha = world.browser("alpha")
        body = alpha.post(f"{REPORTS}/generate").json()
        assert body["status"] == "undeliverable" and body["error_code"] == "report_settings_missing"
        assert "nothing was sent" in body["problem"]
        assert world.rig.channels.fakes == {}

    def test_a_down_channel_is_reported_failed_and_can_be_resent(self, world):
        world.rig.caps.set("alpha", 5, by="admin:root", at=NOW)
        alpha = world.browser("alpha")
        alpha.put(SETTINGS, json=BODY)
        world.rig.channels.unavailable = True
        failed = alpha.post(f"{REPORTS}/generate").json()
        assert failed["status"] == "failed" and failed["sent_at"] is None
        world.rig.channels.unavailable = False
        resent = alpha.post(f"{REPORTS}/2026-10-17/resend")
        assert resent.status_code == 200 and resent.json()["status"] == "sent"
        again = alpha.post(f"{REPORTS}/2026-10-17/resend")
        assert again.status_code == 409
        assert len(world.rig.channels.sent("alpha", "email")) == 1

    def test_a_client_never_reads_or_resends_another_clients_report(self, world):
        world.rig.caps.set("alpha", 5, by="admin:root", at=NOW)
        alpha = world.browser("alpha")
        alpha.put(SETTINGS, json=BODY)
        alpha.post(f"{REPORTS}/generate")
        beta = world.browser("beta")
        assert beta.get(REPORTS).json() == {"reports": []}
        assert beta.get(f"{REPORTS}/2026-10-17").status_code == 404
        assert beta.post(f"{REPORTS}/2026-10-17/resend").status_code == 404
        assert alpha.get(f"{REPORTS}/2026-10-17").status_code == 200

    def test_a_bad_day_is_refused(self, world):
        assert world.browser("alpha").get(f"{REPORTS}/yesterday").status_code == 422

    def test_without_a_database_it_says_so(self, world):
        world.application.daily_reports = None
        alpha = world.browser("alpha")
        assert alpha.get(REPORTING).status_code == 503
        assert alpha.post(f"{REPORTS}/generate").status_code == 503

    def test_the_stored_report_carries_no_secret_or_prospect_address(self, world):
        repo = world.made.repositories["alpha"]
        populate(repo, "alpha")
        world.rig.caps.set("alpha", 5, by="admin:root", at=NOW)
        alpha = world.browser("alpha")
        alpha.put(SETTINGS, json=BODY)
        text = alpha.post(f"{REPORTS}/generate").text
        assert "+10000000000" not in text and "Alex" not in text
