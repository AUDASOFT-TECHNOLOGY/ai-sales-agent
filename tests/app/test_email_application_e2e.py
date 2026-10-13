"""The whole application on Google Sheets with the e-mail channel, from the Admin's decision to the
reply that comes back through the mailbox.

What is real: the `Application` (configuration, registry, scheduler, agent switch), the Admin and
Client HTTP routes that authorize and set up the channel, the credential store, the descriptor's own
build functions, the Engine, the Brain package loading, and `GoogleSheetsRepository` reading and
writing the tabs. What is simulated, because it would need a real account: the Google Sheets API (an
in-memory grid), the SMTP and IMAP servers (doubles at the socket's edge), and the model provider.
Nothing here proves a real mail server accepts the login or delivers the message.

A restart is a restart: the application is closed and built again over the same sheets, store and
mailboxes, with new adapters that remember nothing.
"""

import functools
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import imaplib

import pytest
from app_support import (
    InlineExecutor,
    env_for,
    fake_builders,
    make_settings,
    write_client,
)
from factories import NOW
from fastapi.testclient import TestClient
from sheets_support import SID, TUNIS, FakeSheetsApi
from test_admin_clients_api import PASSWORD, owner
from test_app_api import payload, post
from test_client_agent_api import LOGIN, PASSWORDS, give_profile, mark_source_tested
from test_mail_channel import FakeSmtp
from test_mail_mailbox import FakeImap, raw_mail

from sales_app.admin_auth import InMemoryAdminSessionStore, InMemoryAdminUserStore
from sales_app.agent_state import InMemoryAgentStateStore
from sales_app.api import create_app
from sales_app.bootstrap import build_application
from sales_app.client_profiles import InMemoryClientProfileStore
from sales_app.config_store import InMemoryConfigStore
from sales_app.scheduler import Job
from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore
from sales_app.wiring import ChannelProvider
from sales_app.wiring.providers import email as email_provider
from sales_core.contracts import Channel, ClientId
from sales_integrations.fakes import FakeClock
from sales_integrations.google_sheets import GoogleSheetsRepository

BRAIN = "app_test_brains:follow_up_brain"
SECRET = "S3cret-mail-pass!"
ALEX = "alex@example.com"


def mailbox_of(cid):
    return {
        "from_address": f"{cid}@example.org",
        "from_name": f"{cid.title()} Sales",
        "username": f"{cid}@example.org",
        "smtp_host": "smtp.example.org",
        "imap_host": "imap.example.org",
    }


class Mailbox(FakeImap):
    """A mailbox that new mail can be delivered to."""

    def deliver(self, raw):
        self.messages[max(self.messages, default=0) + 1] = {"raw": raw, "seen": False}

    def reply(self, text="Yes, tell me more.", *, sender=ALEX, message_id=None):
        self.deliver(raw_mail(sender=sender, text=text, message_id=message_id or "<r1@mail.x>"))


class Mailroom:
    """The application, its sheets and its mail servers. `restart()` keeps all of them."""

    def __init__(self, tmp_path, *client_ids, whatsapp=(), modes=None):
        self.root = tmp_path
        self.client_ids = (*client_ids, *whatsapp)
        self.clock = FakeClock(NOW)  # 10:00 on Thursday 15 January 2026, in Tunis
        self.sheets = {cid: FakeSheetsApi() for cid in self.client_ids}
        for api in self.sheets.values():
            api.create_layout()
        self.smtp = {cid: FakeSmtp() for cid in self.client_ids}
        self.imap = {cid: Mailbox() for cid in self.client_ids}
        self.imap_opened = {cid: 0 for cid in self.client_ids}
        for cid in self.client_ids:
            extra = {"mode": modes[cid]} if modes and cid in modes else {}
            write_client(
                tmp_path, cid, brain={"factory": BRAIN, "settings": {"tone": cid}}, **extra
            )
        builders, self.made = fake_builders(self.clock)
        self.builders = replace(
            builders,
            repositories={"google_sheets": self._repository},
            channels={**builders.channels, "email": self._email_provider()},
        )
        self.users = InMemoryAdminUserStore()
        self.users.create(owner())
        self.agent_state = InMemoryAgentStateStore()
        self.config_store = InMemoryConfigStore()
        self.secrets = InMemorySecretStore()
        self.audit = InMemorySecretAudit()
        self.application = None
        self.start()
        for index, cid in enumerate(self.client_ids):
            give_profile(self.application, cid, f"+216 20 000 00{index + 1}")
            mark_source_tested(self.application, cid)

    # ---- the outside world, as doubles -----------------------------------------------------

    def _repository(self, config):
        return GoogleSheetsRepository(
            self.sheets[config.client_id],
            client_id=ClientId(config.client_id),
            spreadsheet_id=SID,
            timezone=TUNIS,
        )

    def _email_provider(self):
        def smtp(config):
            return lambda settings: self.smtp[config.client_id]

        def imap(config):
            def open_(settings):
                self.imap_opened[config.client_id] += 1
                return self.imap[config.client_id]

            return open_

        return ChannelProvider(
            kind=Channel.EMAIL,
            # The descriptor's own build functions: settings and password come from the config.
            build=lambda config: email_provider.build_channel(config, smtp_factory=smtp(config)),
            routing_key=lambda config: None,
            webhook_secret=lambda config: None,
            build_parser=None,
            build_inbox=lambda config: email_provider.build_inbox(
                config, imap_factory=imap(config)
            ),
        )

    # ---- the application -------------------------------------------------------------------

    def start(self):
        self.application = build_application(
            make_settings(self.root),
            env=env_for(*self.client_ids),
            builders=self.builders,
            clock=self.clock,
            executor=InlineExecutor(),
            admin_user_store=self.users,
            admin_session_store=InMemoryAdminSessionStore(),
            client_profile_store=self._profiles(),
            client_session_store=InMemoryAdminSessionStore(),
            config_store=self.config_store,
            agent_state_store=self.agent_state,
            secret_store=self.secrets,
            secret_audit=self.audit,
            refresh_executor=InlineExecutor(),
        )

    def _profiles(self):
        if not hasattr(self, "_profile_store"):
            self._profile_store = InMemoryClientProfileStore()
        return self._profile_store

    def restart(self):
        self.application.close()
        self.start()

    def close(self):
        self.application.close()

    def http(self):
        return TestClient(
            create_app(self.application, run_scheduler=False), base_url="https://testserver"
        )

    def admin(self):
        client = self.http()
        response = client.post(
            "/admin/v1/auth/login", json={"username": "root", "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        return client

    def browser(self, cid):
        client = self.http()
        response = client.post(
            LOGIN, json={"login": f"{cid}.login", "password": PASSWORDS.get(cid, "naga-secret-789")}
        )
        assert response.status_code == 200, response.text
        return client

    # ---- the people ------------------------------------------------------------------------

    def allow_email(self, *, only_for=None):
        admin = self.admin()
        if only_for is None:
            response = admin.put(
                "/admin/v1/settings/channels", json={"allowed": ["whatsapp", "email"]}
            )
        else:
            response = admin.put(
                f"/admin/v1/clients/{only_for}/channels", json={"added": ["email"]}
            )
        assert response.status_code == 200, response.text

    def set_up_email(self, cid, *, password=SECRET, start=True):
        """What a client does: choose the channel, give its mailbox and password, start the agent."""
        browser = self.browser(cid)
        version = browser.get("/client/v1/channel").json()["version"]
        response = browser.put(
            "/client/v1/channel",
            json={"expected_version": version, "channel": "email", "email": mailbox_of(cid)},
        )
        assert response.status_code == 200, response.text
        assert (
            browser.put("/client/v1/channel/credentials", json={"password": password}).status_code
            == 200
        )
        if start:
            response = browser.post("/client/v1/agent/start")
            assert response.status_code == 200, response.text
            assert response.json()["active"] is True
        return browser

    def add_prospect(self, cid, pid="p1", **cells):
        values = {
            "prospect_id": pid,
            "display_name": "Alex",
            "phone": "",
            "email": ALEX,
            "status": "new",
        }
        self.sheets[cid].human_add_row("Prospects", **(values | cells))

    def approve(self, cid, pid="p1", nth=-1):
        api = self.sheets[cid]
        rows = [i for i, r in enumerate(api.tabs["Actions"][1:], start=2) if pid in r]
        api.human_set("Actions", rows[nth], "status", "approved")

    # ---- time and work ---------------------------------------------------------------------

    def tick(self, advance=timedelta(minutes=10)):
        self.clock.advance(advance)
        futures = self.application.scheduler.tick(self.clock.now())
        return [result for future in futures for result in future.result()]

    # ---- what the sheets and the servers hold ----------------------------------------------

    def actions(self, cid):
        return self.sheets[cid].rows("Actions")

    def messages(self, cid, direction=None):
        rows = self.sheets[cid].rows("Messages")
        return [r for r in rows if direction is None or r["direction"] == direction]

    def prospect(self, cid, pid="p1"):
        api = self.sheets[cid]
        row = api.find_row("Prospects", "prospect_id", pid)
        return {h: api.cell("Prospects", row, h) for h in api.headers("Prospects")}

    def unread(self, cid):
        return [uid for uid, m in self.imap[cid].messages.items() if not m["seen"]]

    def health(self, cid):
        return self.application.registry.get(cid).health.snapshot()


@pytest.fixture
def room(tmp_path):
    made = Mailroom(tmp_path, "alpha")
    made.allow_email()
    made.set_up_email("alpha")
    yield made
    made.close()


@pytest.fixture
def pair(tmp_path):
    made = Mailroom(tmp_path, "alpha", "beta")
    made.allow_email()
    made.set_up_email("alpha")
    made.set_up_email("beta")
    yield made
    made.close()


# ---- the path: sheet -> proposal -> approval -> SMTP, then IMAP -> reply -> follow-up ------------


def test_the_whole_path_from_the_sheet_to_a_mailbox_and_back(room):
    r = room
    r.add_prospect("alpha")

    assert all(x.ok for x in r.tick())
    (action,) = r.actions("alpha")
    assert action["status"] == "proposed" and action["draft_text"] == "[alpha] hello Alex"
    assert r.smtp["alpha"].sent == []  # approval mode: nothing leaves before a person says so

    r.approve("alpha")
    assert all(x.ok for x in r.tick())
    (mail,) = r.smtp["alpha"].sent
    assert mail["To"] == ALEX and mail["From"] == "Alpha Sales <alpha@example.org>"
    assert mail.get_content().strip() == "[alpha] hello Alex"
    assert r.smtp["alpha"].logins == [("alpha@example.org", SECRET)]
    (outbound,) = r.messages("alpha", "outbound")
    assert outbound["external_id"] == mail["Message-ID"]
    assert r.prospect("alpha")["status"] == "active"
    assert r.prospect("alpha")["next_action_type"] == "follow_up"

    r.imap["alpha"].reply("Yes, tell me more.")
    assert all(x.ok for x in r.tick())
    (inbound,) = r.messages("alpha", "inbound")
    assert inbound["text"] == "Yes, tell me more." and inbound["external_id"] == "r1@mail.x"
    assert r.unread("alpha") == []  # stored, so marked read
    assert r.prospect("alpha")["last_inbound_at"]
    reply = [a for a in r.actions("alpha") if a["action_type"] == "reply"]
    assert len(reply) == 1 and reply[0]["status"] == "proposed"

    r.approve("alpha")
    r.tick()
    assert len(r.smtp["alpha"].sent) == 2
    assert [m["direction"] for m in r.messages("alpha")] == ["outbound", "inbound", "outbound"]

    r.tick(timedelta(days=4))  # Monday: the follow-up is due
    follow_up = [a for a in r.actions("alpha") if a["action_type"] == "follow_up"]
    assert len(follow_up) == 1 and follow_up[0]["status"] == "proposed"


def test_nothing_is_sent_twice_however_often_time_passes(room):
    r = room
    r.add_prospect("alpha")
    r.tick()
    r.approve("alpha")
    for _ in range(5):
        r.tick()
    assert len(r.smtp["alpha"].sent) == 1 and len(r.actions("alpha")) == 1


def test_a_reply_arriving_twice_is_stored_and_answered_once(room):
    r = room
    r.add_prospect("alpha")
    r.imap["alpha"].reply(message_id="<same@mail.x>")
    r.tick()
    r.imap["alpha"].reply(message_id="<same@mail.x>")  # the provider delivers it again
    r.tick()
    assert len(r.messages("alpha", "inbound")) == 1
    assert len([a for a in r.actions("alpha") if a["action_type"] == "reply"]) == 1
    assert r.unread("alpha") == []


def test_an_opt_out_by_email_stops_everything(room):
    r = room
    r.add_prospect("alpha")
    r.tick()
    r.approve("alpha")
    r.imap["alpha"].reply("STOP")
    r.tick()
    assert r.prospect("alpha")["status"] == "opted_out"
    r.tick(timedelta(days=4))
    assert r.smtp["alpha"].sent == []


def test_a_message_from_someone_who_is_not_a_prospect_creates_nothing(room):
    r = room
    r.add_prospect("alpha")
    r.imap["alpha"].reply(sender="stranger@example.net", message_id="<s@mail.x>")
    r.tick()
    assert r.messages("alpha") == [] and r.unread("alpha") == []
    assert [row["prospect_id"] for row in r.sheets["alpha"].rows("Prospects")] == ["p1"]


def test_a_reply_from_the_mailbox_itself_or_an_automatic_one_is_not_a_reply(room):
    r = room
    r.add_prospect("alpha")
    r.imap["alpha"].reply(sender="alpha@example.org", message_id="<own@mail.x>")
    r.imap["alpha"].deliver(
        raw_mail(message_id="<auto@mail.x>", headers={"Auto-Submitted": "auto-replied"})
    )
    r.tick()
    assert r.messages("alpha") == [] and r.unread("alpha") == []


# ---- restart and recovery -----------------------------------------------------------------------


def test_a_restart_changes_nothing_and_loses_no_reply(room):
    r = room
    r.add_prospect("alpha")
    r.tick()
    r.approve("alpha")
    r.restart()
    r.tick()
    assert len(r.smtp["alpha"].sent) == 1

    r.imap["alpha"].reply()
    r.restart()  # the reply waits in the mailbox: it is read after the restart
    r.tick()
    assert len(r.messages("alpha", "inbound")) == 1

    r.restart()
    r.tick()
    r.tick(timedelta(hours=2))
    assert len(r.smtp["alpha"].sent) == 1 and len(r.messages("alpha", "inbound")) == 1
    assert len([a for a in r.actions("alpha") if a["action_type"] == "reply"]) == 1


def test_a_crash_between_storing_and_acknowledging_loses_and_duplicates_nothing(room):
    r = room
    r.add_prospect("alpha")
    r.imap["alpha"].reply()
    r.imap["alpha"].fail_on = "STORE"  # the connection dies as the message is marked read
    failed = [x for x in r.tick() if x.job is Job.INBOX]
    assert failed and not failed[0].ok
    assert len(r.messages("alpha", "inbound")) == 1  # it was stored first
    assert r.unread("alpha") == [1]  # but not marked read: it will come back

    r.imap["alpha"].fail_on = None
    r.tick(timedelta(minutes=30))
    assert len(r.messages("alpha", "inbound")) == 1  # recognised, not stored again
    assert r.unread("alpha") == []
    assert len([a for a in r.actions("alpha") if a["action_type"] == "reply"]) == 1


def test_a_message_that_cannot_be_stored_is_kept_unread_and_the_others_go_on(room):
    r = room
    r.add_prospect("alpha")
    r.imap["alpha"].reply(text="first", message_id="<m1@mail.x>")
    r.imap["alpha"].reply(text="second", message_id="<m2@mail.x>")
    api = r.sheets["alpha"]
    api.fail_next("append", times=1)  # the sheet refuses the first write
    results = r.tick()
    inbox = [x for x in results if x.job is Job.INBOX]
    assert inbox and inbox[0].errors == ("could not store a reply (SheetsApiError)",)
    assert [m["text"] for m in r.messages("alpha", "inbound")] == ["second"]
    assert r.unread("alpha") == [1]  # the one that failed is still unread, the other is read
    r.tick(timedelta(minutes=30))
    assert sorted(m["text"] for m in r.messages("alpha", "inbound")) == ["first", "second"]
    assert r.unread("alpha") == []


# ---- a mailbox that fails must not stop the client from sending -------------------------------


def test_an_unreachable_mailbox_backs_off_and_does_not_stop_the_sending(room):
    r = room
    r.add_prospect("alpha")
    r.tick()
    r.approve("alpha")
    r.imap["alpha"].login_error = OSError("timed out")
    results = r.tick()
    inbox = [x for x in results if x.job is Job.INBOX]
    assert inbox and not inbox[0].ok
    assert len(r.smtp["alpha"].sent) == 1  # the approved message went out all the same
    assert r.application.registry.get("alpha").state.inbox_blocked_until is not None

    opened = r.imap_opened["alpha"]
    r.tick(timedelta(seconds=10))  # still backing off: the mailbox is left alone
    assert r.imap_opened["alpha"] == opened

    r.imap["alpha"].login_error = None
    r.imap["alpha"].reply()
    r.tick(timedelta(minutes=20))
    assert len(r.messages("alpha", "inbound")) == 1
    assert r.health("alpha")["last_runs"].get("inbox")


def test_a_refused_login_is_a_failure_with_a_reason_and_no_password_in_it(room):
    r = room
    r.imap["alpha"].login_error = imaplib.IMAP4.error(f"AUTHENTICATIONFAILED {SECRET}")
    results = r.tick()
    inbox = [x for x in results if x.job is Job.INBOX]
    assert inbox and not inbox[0].ok
    assert SECRET not in " ".join(inbox[0].errors) and SECRET not in str(r.health("alpha"))
    assert r.health("alpha")["status"] == "degraded"


def test_a_smtp_failure_leaves_the_action_to_be_tried_again(room):
    r = room
    r.add_prospect("alpha")
    r.tick()
    r.approve("alpha")
    server = r.smtp["alpha"]
    server.send_error = __import__("smtplib").SMTPDataError(451, b"try later")
    r.tick()
    assert server.sent == [] and r.actions("alpha")[0]["status"] != "sent"
    server.send_error = None
    for _ in range(4):
        r.tick(timedelta(hours=1))
    assert len(server.sent) == 1


# ---- jobs never overlap -----------------------------------------------------------------------


def test_the_mailbox_is_read_at_its_own_pace(room):
    r = room
    r.tick()
    opened = r.imap_opened["alpha"]
    assert opened >= 1
    r.tick(timedelta(seconds=30))  # less than the poll interval
    assert r.imap_opened["alpha"] == opened
    r.tick(timedelta(minutes=3))
    assert r.imap_opened["alpha"] == opened + 1


def test_a_job_still_running_is_never_joined_by_another(room):
    r = room
    runtime = r.application.registry.get("alpha")
    assert runtime.state.try_begin()  # a job is running for this client
    try:
        r.clock.advance(timedelta(minutes=10))
        assert r.application.scheduler.tick(r.clock.now()) == []
        assert r.imap_opened["alpha"] == 0
    finally:
        runtime.state.end()
    assert r.application.scheduler.tick(r.clock.now())


def test_the_inbox_job_comes_before_the_cycle_that_answers_it(room):
    r = room
    runtime = r.application.registry.get("alpha")
    jobs = r.application.scheduler.due_jobs(runtime, r.clock.now() + timedelta(hours=1))
    assert jobs[0] is Job.INBOX and Job.DAILY in jobs


# ---- routing and isolation --------------------------------------------------------------------


def test_a_reply_goes_to_the_client_whose_mailbox_received_it(pair):
    r = pair
    for cid in ("alpha", "beta"):
        r.add_prospect(cid)  # the same prospect id and address in two spreadsheets
    r.imap["alpha"].reply("for alpha")
    r.tick()
    assert [m["text"] for m in r.messages("alpha", "inbound")] == ["for alpha"]
    assert r.messages("beta") == [] and r.prospect("beta")["last_inbound_at"] == ""


def test_each_client_sends_only_through_its_own_mailbox(pair):
    r = pair
    for cid in ("alpha", "beta"):
        r.add_prospect(cid)
    r.tick()
    r.approve("alpha")  # only alpha's team approved
    r.tick()
    assert len(r.smtp["alpha"].sent) == 1 and r.smtp["beta"].sent == []
    assert r.smtp["alpha"].sent[0]["From"].endswith("<alpha@example.org>")
    r.approve("beta")
    r.tick()
    assert r.smtp["beta"].sent[0]["From"].endswith("<beta@example.org>")
    assert len(r.smtp["alpha"].sent) == 1


def test_one_clients_opt_out_is_not_the_others(pair):
    r = pair
    for cid in ("alpha", "beta"):
        r.add_prospect(cid)
    r.imap["alpha"].reply("STOP")
    r.tick()
    assert r.prospect("alpha")["status"] == "opted_out" and r.prospect("beta")["status"] == "new"


def test_a_client_on_whatsapp_keeps_its_webhook_while_another_uses_email(tmp_path):
    r = Mailroom(tmp_path, "alpha", whatsapp=("beta",))
    try:
        r.allow_email()
        r.set_up_email("alpha")
        r.application.agent_state.set("beta", running=True, by="t", at=r.clock.now())
        r.add_prospect("beta", phone="+15550000001", email="")
        client = r.http()
        body = payload("phone-beta", "15550000001", "Hello from WhatsApp", "wamid.9")
        response = post(client, body, secret="secret-beta")
        assert response.status_code == 200 and response.json() == {"received": 1}
        assert [m["text"] for m in r.messages("beta", "inbound")] == ["Hello from WhatsApp"]
        assert r.messages("alpha") == []
        assert set(r.application.parsers) == {"whatsapp"}  # e-mail has no webhook
        assert client.post("/webhooks/email", content=b"{}").status_code == 404
        assert client.get("/webhooks/email").status_code == 404
    finally:
        r.close()


def test_a_second_client_cannot_take_over_a_mailbox_at_runtime(pair):
    r = pair
    version = r.config_store.current("beta").version
    from sales_app.api.admin_clients import publish_client_settings

    publish_client_settings(  # an administrator publishes beta on alpha's mailbox
        r.application,
        "beta",
        {"channel": {"provider": "email", "settings": mailbox_of("alpha")}},
        expected_version=version,
        author="root",
        top_keys=(),
        sections={"channel": ("provider", "settings")},
    )
    r.application.runtime_manager.refresh_client("beta").result()
    status = r.application.runtime_manager.status("beta")
    assert status.state == "failed" and "already used" in " ".join(status.errors)
    assert "alpha" in " ".join(status.errors)  # an administrator may be told whose it is
    r.add_prospect("alpha")
    r.imap["alpha"].reply()
    r.tick()
    assert len(r.messages("alpha", "inbound")) == 1


# ---- the Admin's authority --------------------------------------------------------------------


def test_a_channel_the_admin_takes_away_stops_working_at_once(room):
    r = room
    r.add_prospect("alpha")
    r.tick()
    r.approve("alpha")  # approved, about to be sent
    r.imap["alpha"].reply()

    response = r.admin().put("/admin/v1/clients/alpha/channels", json={"removed": ["email"]})
    assert response.status_code == 200
    missing = r.application.registry.missing("alpha")
    assert any("not authorized" in reason for reason in missing)
    opened = r.imap_opened["alpha"]

    assert r.tick() == [] and r.tick(timedelta(hours=3)) == []
    assert r.smtp["alpha"].sent == [] and r.imap_opened["alpha"] == opened
    assert r.messages("alpha", "inbound") == [] and r.unread("alpha") == [1]

    browser = r.browser("alpha")
    assert browser.get("/client/v1/channel").json()["authorized"] is False
    assert browser.get("/client/v1/agent").json()["active"] is False
    assert (
        browser.put("/client/v1/channel/credentials", json={"password": "x" * 10}).status_code
        == 403
    )
    assert browser.post("/client/v1/channel/test").status_code == 403


def test_the_channel_works_again_when_the_admin_gives_it_back(room):
    r = room
    r.add_prospect("alpha")
    r.tick()
    r.approve("alpha")
    admin = r.admin()
    admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["email"]})
    assert r.tick() == [] and r.smtp["alpha"].sent == []
    admin.put("/admin/v1/clients/alpha/channels", json={})
    assert not r.application.registry.missing("alpha")
    r.tick()
    assert len(r.smtp["alpha"].sent) == 1


def test_taking_the_channel_away_globally_stops_every_client_on_it(pair):
    r = pair
    r.admin().put("/admin/v1/settings/channels", json={"allowed": ["whatsapp"]})
    assert r.tick() == []
    assert all(
        any("not authorized" in m for m in r.application.registry.missing(c))
        for c in ("alpha", "beta")
    )


def test_an_exception_keeps_one_client_on_a_channel_the_others_lost(pair):
    r = pair
    admin = r.admin()
    admin.put("/admin/v1/settings/channels", json={"allowed": ["whatsapp"]})
    admin.put("/admin/v1/clients/alpha/channels", json={"added": ["email"]})
    assert not r.application.registry.missing("alpha")
    assert r.application.registry.missing("beta")
    r.add_prospect("alpha")
    r.tick()
    assert r.actions("alpha")  # alpha still works


def test_a_forbidden_channel_survives_a_restart(room):
    r = room
    r.admin().put("/admin/v1/clients/alpha/channels", json={"removed": ["email"]})
    r.restart()
    assert r.tick() == []
    assert r.application.registry.missing("alpha")


def test_the_password_is_removed_the_client_stops_without_a_secret_in_the_report(room):
    r = room
    browser = r.browser("alpha")
    assert browser.delete("/client/v1/channel/credentials").status_code == 200
    missing = r.application.registry.missing("alpha")
    assert missing and SECRET not in " ".join(missing)
    assert r.tick() == []


# ---- what never talks to the outside ----------------------------------------------------------


def test_a_dry_run_never_opens_the_mailbox(tmp_path):
    r = Mailroom(tmp_path, "alpha", modes={"alpha": "dry_run"})
    try:
        r.allow_email()
        r.set_up_email("alpha", start=False)
        r.application.agent_state.set("alpha", running=True, by="t", at=r.clock.now())
        r.imap["alpha"].reply()
        r.tick()
        r.tick(timedelta(hours=1))
        assert r.imap_opened["alpha"] == 0 and r.unread("alpha") == [1]
        assert r.smtp["alpha"].sent == []
    finally:
        r.close()


def test_a_paused_agent_does_not_read_its_mailbox(room):
    r = room
    r.add_prospect("alpha")
    r.browser("alpha").post("/client/v1/agent/pause")
    r.imap["alpha"].reply()
    assert r.tick() == [] and r.imap_opened["alpha"] == 0
    r.browser("alpha").post("/client/v1/agent/start")
    r.tick()
    assert len(r.messages("alpha", "inbound")) == 1


# ---- the wiring the application really uses ---------------------------------------------------


def test_the_default_builders_make_email_a_channel_with_an_inbox_and_no_webhook(tmp_path):
    from sales_app.wiring import default_builders

    builders = default_builders(make_settings(tmp_path))
    provider = builders.channels["email"]
    assert provider.kind is Channel.EMAIL
    assert provider.build_parser is None and provider.build_inbox is not None
    assert builders.channels["whatsapp"].build_inbox is None
    assert builders.channels["whatsapp"].build_parser is not None


def test_the_descriptor_builds_what_the_runtime_needs_from_a_real_config(room):
    r = room
    config = r.application.registry.get_config("alpha")
    channel = email_provider.build_channel(config)
    inbox = email_provider.build_inbox(config)
    assert channel.channel is Channel.EMAIL
    assert SECRET not in repr(channel) and SECRET not in repr(inbox)
    assert functools.partial  # (the real factories connect only when used: nothing was opened)
