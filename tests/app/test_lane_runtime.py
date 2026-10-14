"""E5: a client that runs several sources and channels at once, from its configuration to its Engine.

What is real: the `Application` (configuration loading, credential resolution, registry, webhook
parsers, scheduler), the HTTP webhook route with the WhatsApp and Telegram parsers, the real SQL
operational store over a real migrated database, the real consent registry, `CompositeRepository`,
`ChannelRouter` and the Engine.

What is simulated, because it would need a real account: every source (an in-memory double named by
its instance id, as a real one is), every channel adapter (a fake that records what it was asked to
send) and the mailbox. Nothing here proves that a real Google Sheet, HubSpot account, WhatsApp
number, Telegram bot or mailbox behaves like these doubles.
"""

import json
from dataclasses import dataclass, replace

import pytest
import yaml
from app_support import InlineExecutor, client_yaml, env_for, fake_builders, make_settings
from consent_support import make_world
from factories import NOW, make_prospect
from fastapi.testclient import TestClient
from sourcing_fakes import FakeProspectSource, default_capabilities
from sql_store_support import SqlStoreUnderTest
from test_config_lanes import EMAIL_SETTINGS, HUBSPOT, LANES, SOURCES, SPEC_FACTORY, catalog

from sales_app.agent_state import InMemoryAgentStateStore
from sales_app.api import create_app
from sales_app.bootstrap import build_application
from sales_app.config import import_document
from sales_app.config_store import InMemoryConfigStore
from sales_app.errors import ConfigurationError, RuntimeBuildError
from sales_app.parsers import build_parsers
from sales_app.scheduler import Job
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    InMemorySecretAudit,
    InMemorySecretStore,
    client_scope,
)
from sales_app.sourcing import StatusMapping
from sales_app.wiring import build_runtime, default_builders
from sales_core.contracts import (
    Channel,
    ClientId,
    ContactInfo,
    InboundMessage,
    ProspectId,
)
from sales_integrations.fakes import FakeClock, FakeMessagingChannel
from sales_integrations.telegram import SECRET_HEADER
from sales_integrations.whatsapp import compute_signature

MAPPING = StatusMapping.from_config({"lead": "new", "hold": "paused", "dnc": "opted_out"})
PHONE = "+21620000001"
EMAIL = "lead@example.org"
EMAIL_CHANNEL = {"provider": "email", "settings": EMAIL_SETTINGS}


# ---- a client's configuration, secrets and doubles ----------------------------------------------


def raw_for(cid, *, sources=SOURCES, lanes=LANES, channel=EMAIL_CHANNEL, **overrides):
    """A `client.yaml` with lanes whose Brain has the products a, b and c."""
    data = client_yaml(cid)
    data["brain"] = {"factory": SPEC_FACTORY, "settings": catalog("a", "b", "c")}
    data["sources"], data["lanes"] = [dict(s) for s in sources], [dict(lane) for lane in lanes]
    if channel is not None:
        data["channel"] = channel
    return {**data, **overrides}


def other_client(cid, spreadsheet, lane_channel="whatsapp", *, mailbox=None):
    """A second client that runs lanes of its own, on a source of its own."""
    settings = {"spreadsheet_id": spreadsheet}
    sources = [{"id": "sheet_x", "provider": "google_sheets", "settings": settings}]
    lanes = [{"id": "main", "source": "sheet_x", "channel": lane_channel, "products": ["a"]}]
    return raw_for(cid, sources=sources, lanes=lanes, channel=mailbox)


def bot_token(index: int, cid: str) -> str:
    return f"{1000000 + index}:AAH-{cid}-token-value-of-the-bot-padding-xx"


def webhook_secret(cid: str) -> str:
    return f"{cid}-" + "s" * 40


def hubspot_token(index: int) -> str:
    return f"pat-eu1-{index:08x}-1111-1111-1111-111111111111"


def give(secrets, index, cid, *, email=True, telegram=True, hubspot=True):
    scope = client_scope(cid)
    if email:
        secrets.set(scope, "email.password", f"mailbox-password-{cid}", by="test")
    if telegram:
        secrets.set(scope, "telegram.bot_token", bot_token(index, cid), by="test")
        secrets.set(scope, "telegram.webhook_secret", webhook_secret(cid), by="test")
    if hubspot:
        secrets.set(scope, "hubspot.access_token", hubspot_token(index), by="test")


class Mailbox:
    """The unread mail of one client, as `InboxPoller` wants it."""

    def __init__(self) -> None:
        self.unread: list = []
        self.acknowledged: list[str] = []
        self.closed = 0

    def deliver(self, sender, text, external_id) -> None:
        message = InboundMessage(
            channel=Channel.EMAIL, sender=sender, external_id=external_id, text=text, timestamp=NOW
        )
        self.unread.append(_Fetched(external_id, message))

    def fetch(self, limit=50):
        return list(self.unread)[:limit]

    def acknowledge(self, handle):
        self.acknowledged.append(handle)
        self.unread = [item for item in self.unread if item.handle != handle]

    def close(self):
        self.closed += 1


@dataclass(frozen=True)
class _Fetched:
    handle: str
    message: InboundMessage


class Deployment:
    """One deployment: an application over one database, with doubles for everything outside."""

    def __init__(self, root, engine, clients, secrets, *, mode_of=None, publish=True):
        self.root, self.engine = root, engine
        self.clients = dict(clients)
        self.secrets = secrets
        self.clock = FakeClock(NOW)
        self.config_store = InMemoryConfigStore()
        self.agent_state = InMemoryAgentStateStore()
        self.audit = InMemorySecretAudit()
        self.stores = {c: SqlStoreUnderTest(engine, c) for c in self.clients}
        self.worlds = {c: make_world(client_id=c) for c in self.clients}
        self.sources: dict[tuple[str, str], FakeProspectSource] = {}
        self.channels: dict[tuple[str, Channel], FakeMessagingChannel] = {}
        self.mailboxes = {c: Mailbox() for c in self.clients}
        self.inboxes_opened = {c: 0 for c in self.clients}
        self.failing_sources: dict[str, str] = {}
        self.failing_channels: dict[str, str] = {}
        for cid, raw in self.clients.items():
            (root / cid).mkdir(parents=True, exist_ok=True)
            (root / cid / "client.yaml").write_text(
                yaml.safe_dump({**raw, **({"mode": mode_of[cid]} if mode_of else {})}),
                encoding="utf-8",
            )
            if publish:  # else the settings stay in client.yaml, which nothing validated before
                check = import_document(cid, {**raw, **({"mode": mode_of[cid]} if mode_of else {})})
                assert check.errors == (), check.errors
                self.config_store.publish(cid, check.document, author="test", expected_parent=None)
        self.application = None
        self.start()

    # ---- the outside world, as doubles --------------------------------------------------

    def source(self, cid: str, source_id: str) -> FakeProspectSource:
        key = (cid, source_id)
        if key not in self.sources:
            self.sources[key] = FakeProspectSource(
                ClientId(cid),
                capabilities=default_capabilities(id_namespace=source_id),
                status_mapping=MAPPING,
            )
        return self.sources[key]

    def channel(self, cid: str, kind: Channel) -> FakeMessagingChannel:
        key = (cid, kind)
        if key not in self.channels:
            self.channels[key] = FakeMessagingChannel(kind, clock=self.clock)
        return self.channels[key]

    def add_prospect(self, cid, source_id, native_id, **contact):
        self.source(cid, source_id).add_record(
            make_prospect(
                prospect_id=ProspectId(native_id),
                client_id=ClientId(cid),
                contact=ContactInfo(**contact),
            ),
            raw_status="lead",
        )
        return f"{source_id}:{native_id}"

    def builders(self):
        base, _ = fake_builders(self.clock)
        real = default_builders(make_settings(self.root)).channels

        def build_source(config, instance):
            if instance.id in self.failing_sources:
                raise ValueError(self.failing_sources[instance.id])
            return self.source(config.client_id, instance.id)

        def channel_builder(kind):
            def build(config):
                if kind.value in self.failing_channels:
                    raise ValueError(self.failing_channels[kind.value])
                return self.channel(config.client_id, kind)

            return build

        def open_inbox(config):
            self.inboxes_opened[config.client_id] += 1
            return self.mailboxes[config.client_id]

        channels = {
            "whatsapp": replace(real["whatsapp"], build=channel_builder(Channel.WHATSAPP)),
            "telegram": replace(real["telegram"], build=channel_builder(Channel.TELEGRAM)),
            "email": replace(
                real["email"],
                build=channel_builder(Channel.EMAIL),
                build_inbox=open_inbox,
            ),
        }
        return replace(
            base,
            channels=channels,
            source_instances={"google_sheets": build_source, "hubspot": build_source},
            operational_stores=lambda cid: self.stores[cid],
            consent=lambda cid: self.worlds[cid].registry,
        )

    # ---- the application ----------------------------------------------------------------

    def start(self):
        self.application = build_application(
            make_settings(self.root),
            env=env_for(*self.clients),
            builders=self.builders(),
            clock=self.clock,
            executor=InlineExecutor(),
            config_store=self.config_store,
            agent_state_store=self.agent_state,
            secret_store=self.secrets,
            secret_audit=self.audit,
            refresh_executor=InlineExecutor(),
        )
        for cid in self.clients:
            self.agent_state.set(cid, running=True, by="tests", at=self.clock.now())

    def restart(self):
        self.application.close()
        self.start()

    def close(self):
        self.application.close()

    def runtime(self, cid):
        runtime = self.application.registry.get(cid)
        assert runtime is not None and not runtime.suspended, self.application.registry.health()
        return runtime

    def http(self):
        return TestClient(create_app(self.application, run_scheduler=False))

    def reconfigure(self, cid, **changes):
        """Publish a new version of a client's settings: what its Admin would do."""
        raw = {**self.clients[cid], **changes}
        check = import_document(cid, raw)
        assert check.errors == (), check.errors
        current = self.config_store.current(cid)
        self.config_store.publish(
            cid, check.document, author="test", expected_parent=current.version
        )
        self.clients[cid] = raw


def secrets_for(*cids, **what):
    secrets = InMemorySecretStore()
    secrets.set(GLOBAL_SCOPE, "channels.allowed", "whatsapp,email,telegram", by="test")
    for index, cid in enumerate(cids):
        give(secrets, index + 1, cid, **what)
    return secrets


@pytest.fixture
def deploy(tmp_path, sql_engine):
    made: list[Deployment] = []

    def make(clients, secrets=None, **kwargs):
        deployment = Deployment(
            tmp_path, sql_engine, clients, secrets or secrets_for(*clients), **kwargs
        )
        made.append(deployment)
        return deployment

    yield make
    for deployment in made:
        deployment.close()


def whatsapp_event(phone_id, sender, text, wamid):
    value = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "100", "phone_number_id": phone_id},
        "messages": [
            {
                "from": sender,
                "id": wamid,
                "timestamp": "1768467600",
                "type": "text",
                "text": {"body": text},
            }
        ],
    }
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba", "changes": [{"field": "messages", "value": value}]}],
    }


def post_whatsapp(client, body, secret):
    raw = json.dumps(body).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": compute_signature(secret, raw),
    }
    return client.post("/webhooks/whatsapp", content=raw, headers=headers)


def approve_everything(engine):
    for proposal in engine.pending_proposals():
        engine.approve_proposal(proposal.action_id, actor="test")


# ---- one client, three circuits ---------------------------------------------------------------


def test_a_client_with_three_lanes_runs_each_circuit_on_its_own_channel(deploy):
    d = deploy({"alpha": raw_for("alpha")})
    a = d.add_prospect("alpha", "sheet_a", "1", phone=PHONE)
    crm = d.add_prospect("alpha", "hubspot", "7", email=EMAIL)
    b = d.add_prospect("alpha", "sheet_b", "1", phone="+21620000002")

    with d.runtime("alpha").lease() as engine:
        engine.prepare()
        approve_everything(engine)
        engine.dispatch()
        # a Telegram bot cannot write first: the sheet B person is waiting to start the bot
        waiting = {p.prospect_id for p in engine.prospects_awaiting_opt_in()}

    assert [m.prospect_id for m in d.channel("alpha", Channel.WHATSAPP).sent] == [a]
    assert [m.prospect_id for m in d.channel("alpha", Channel.EMAIL).sent] == [crm]
    assert d.channel("alpha", Channel.TELEGRAM).sent == ()
    assert waiting == {b}
    lanes = {a_.prospect_id: (a_.lane_id, a_.channel) for a_ in d.stores["alpha"].list_actions()}
    assert lanes == {
        a: ("a", Channel.WHATSAPP),
        crm: ("crm", Channel.EMAIL),
    }


def test_sources_keep_their_own_ids_and_never_meet(deploy):
    d = deploy({"alpha": raw_for("alpha")})
    d.add_prospect("alpha", "sheet_a", "1", phone=PHONE)
    d.add_prospect("alpha", "sheet_b", "1", phone="+21620000002")  # same native id, other sheet
    d.add_prospect("alpha", "hubspot", "1", email=EMAIL)
    with d.runtime("alpha").lease() as engine:
        engine.prepare()
    repository = d.application.registry.get("alpha").engine._repo
    assert sorted(p.prospect_id for p in repository.list_prospects()) == [
        "hubspot:1",
        "sheet_a:1",
        "sheet_b:1",
    ]


def test_in_dry_run_every_channel_is_disabled_and_no_mailbox_is_read(deploy):
    d = deploy({"alpha": raw_for("alpha")}, mode_of={"alpha": "dry_run"})
    d.add_prospect("alpha", "sheet_a", "1", phone=PHONE)
    d.add_prospect("alpha", "hubspot", "7", email=EMAIL)
    runtime = d.runtime("alpha")
    with runtime.lease() as engine:
        engine.prepare()
        engine.dispatch()
    assert d.channels == {}  # no adapter was even built: a dry run cannot send
    assert not d.application.builders.polls_inbox(runtime.config)
    d.application.scheduler.run_job_now(runtime, Job.RECOVER)
    assert d.inboxes_opened["alpha"] == 0


# ---- what comes in goes to the right client and engine ------------------------------------------


def test_a_whatsapp_message_reaches_only_the_client_that_owns_the_number(deploy):
    d = deploy({"alpha": raw_for("alpha"), "beta": other_client("beta", "sheet-beta")})
    d.add_prospect("alpha", "sheet_a", "1", phone=PHONE)
    d.add_prospect("beta", "sheet_x", "1", phone=PHONE)  # the same person in another client
    http = d.http()

    response = post_whatsapp(
        http, whatsapp_event("phone-alpha", PHONE.lstrip("+"), "hello alpha", "w1"), "secret-alpha"
    )
    assert response.status_code == 200 and response.json() == {"received": 1}

    def inbound(cid, pid):
        with d.runtime(cid).lease() as engine:
            return [m.text for m in engine._repo.get_conversation(ProspectId(pid)).messages]

    assert inbound("alpha", "sheet_a:1") == ["hello alpha"]
    assert inbound("beta", "sheet_x:1") == []
    # and the other way round, signed with its own secret
    post_whatsapp(
        http, whatsapp_event("phone-beta", PHONE.lstrip("+"), "hello beta", "w2"), "secret-beta"
    )
    assert inbound("beta", "sheet_x:1") == ["hello beta"]
    assert inbound("alpha", "sheet_a:1") == ["hello alpha"]


def test_a_message_signed_with_another_clients_secret_is_refused(deploy):
    d = deploy({"alpha": raw_for("alpha"), "beta": other_client("beta", "sheet-beta")})
    response = post_whatsapp(
        d.http(), whatsapp_event("phone-alpha", "21620000001", "hi", "w9"), "secret-beta"
    )
    assert response.status_code == 401


def test_a_telegram_update_is_routed_by_the_bot_of_the_client_that_owns_it(deploy):
    d = deploy({"alpha": raw_for("alpha"), "beta": other_client("beta", "sheet-beta", "telegram")})
    registry = d.application.registry
    assert registry.by_routing_key("telegram", "1000001").client_id == "alpha"
    assert registry.by_routing_key("telegram", "1000002").client_id == "beta"
    assert registry.by_routing_key("telegram", "999") is None
    assert registry.by_routing_key("whatsapp", "phone-alpha").client_id == "alpha"
    assert registry.by_routing_key("whatsapp", "phone-beta") is None  # beta runs no WhatsApp lane

    update = {
        "update_id": 1,
        "message": {
            "message_id": 5,
            "date": 1768467600,
            "chat": {"id": 4242, "type": "private"},
            "from": {"id": 4242, "is_bot": False, "first_name": "Ada"},
            "text": "hi",
        },
    }
    secret = webhook_secret("alpha")
    response = d.http().post(
        "/webhooks/telegram",
        content=json.dumps(update).encode(),
        headers={"Content-Type": "application/json", SECRET_HEADER: secret},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"received": 1}


def test_the_webhook_parsers_exist_for_the_channels_that_call_us_only(deploy):
    d = deploy({"alpha": raw_for("alpha")})
    configs = [d.application.registry.get_config("alpha")]
    assert sorted(build_parsers(configs, d.application.builders)) == ["telegram", "whatsapp"]


def test_a_mailbox_is_read_once_for_a_client_however_many_lanes_use_it(deploy):
    lanes = [*LANES[:2], {**LANES[2], "channel": "email"}]  # two lanes on e-mail
    d = deploy({"alpha": raw_for("alpha", lanes=lanes)})
    crm = d.add_prospect("alpha", "hubspot", "7", email=EMAIL)
    d.mailboxes["alpha"].deliver(EMAIL, "yes please", "mail-1")
    runtime = d.runtime("alpha")
    assert d.application.builders.polls_inbox(runtime.config)

    result = d.application.scheduler.run_job_now(runtime, Job.INBOX)

    assert result.ok and result.errors == (), result
    assert d.inboxes_opened["alpha"] == 1 and d.mailboxes["alpha"].closed == 1
    assert d.mailboxes["alpha"].acknowledged == ["mail-1"]
    with runtime.lease() as engine:
        assert [m.text for m in engine._repo.get_conversation(ProspectId(crm)).messages] == [
            "yes please"
        ]


def test_a_client_with_no_email_lane_has_no_mailbox_to_read(deploy):
    lanes = [lane for lane in LANES if lane["channel"] != "email"]
    d = deploy(
        {"alpha": raw_for("alpha", sources=[SOURCES[0], SOURCES[2]], lanes=lanes, channel=None)}
    )
    assert not d.application.builders.polls_inbox(d.runtime("alpha").config)


def test_a_reply_leaves_on_the_channel_it_came_in_on(deploy):
    d = deploy({"alpha": raw_for("alpha")})
    # a sheet A person (a WhatsApp lane) who writes by e-mail, which the client also runs
    a = d.add_prospect("alpha", "sheet_a", "1", phone=PHONE, email=EMAIL)
    d.mailboxes["alpha"].deliver(EMAIL, "write to me here", "mail-7")
    runtime = d.runtime("alpha")
    d.application.scheduler.run_job_now(runtime, Job.INBOX)
    with runtime.lease() as engine:
        approve_everything(engine)
        engine.dispatch()
    assert [m.prospect_id for m in d.channel("alpha", Channel.EMAIL).sent] == [a]
    assert d.channel("alpha", Channel.WHATSAPP).sent == ()


def test_a_reply_to_a_whatsapp_message_leaves_on_whatsapp(deploy):
    d = deploy({"alpha": raw_for("alpha")})
    a = d.add_prospect("alpha", "sheet_a", "1", phone=PHONE, email=EMAIL)
    post_whatsapp(
        d.http(), whatsapp_event("phone-alpha", PHONE.lstrip("+"), "hello", "w5"), "secret-alpha"
    )
    with d.runtime("alpha").lease() as engine:
        approve_everything(engine)
        engine.dispatch()
    assert [m.prospect_id for m in d.channel("alpha", Channel.WHATSAPP).sent] == [a]
    assert d.channel("alpha", Channel.EMAIL).sent == ()


# ---- a planned action keeps its channel after a reconfiguration ---------------------------------


def test_a_planned_action_keeps_its_channel_when_the_lanes_are_reconfigured(deploy):
    d = deploy({"alpha": raw_for("alpha")})
    crm = d.add_prospect("alpha", "hubspot", "7", email=EMAIL)
    d.add_prospect("alpha", "sheet_b", "1", phone="+21620000002")
    with d.runtime("alpha").lease() as engine:
        engine.prepare()  # approval mode: the e-mail to the CRM person waits
        assert [p.prospect_id for p in engine.pending_proposals()] == [crm]

    # the CRM lane now writes on WhatsApp, and sheet B on e-mail: e-mail is still a channel it runs
    lanes = [
        {**LANES[0]},
        {**LANES[1], "channel": "whatsapp"},
        {**LANES[2], "channel": "email"},
    ]
    d.reconfigure("alpha", lanes=lanes)
    d.restart()

    with d.runtime("alpha").lease() as engine:
        approve_everything(engine)
        engine.dispatch()
    assert [m.prospect_id for m in d.channel("alpha", Channel.EMAIL).sent] == [crm]
    assert crm not in {m.prospect_id for m in d.channel("alpha", Channel.WHATSAPP).sent}


def test_a_reconfiguration_applied_while_running_keeps_what_was_planned_on_its_channel(deploy):
    d = deploy({"alpha": raw_for("alpha")})
    crm = d.add_prospect("alpha", "hubspot", "7", email=EMAIL)
    with d.runtime("alpha").lease() as engine:
        engine.prepare()
    lanes = [{**LANES[0]}, {**LANES[1], "channel": "whatsapp"}, {**LANES[2], "channel": "email"}]
    d.reconfigure("alpha", lanes=lanes)
    d.application.runtime_manager.refresh_client("alpha").result()
    assert d.application.runtime_manager.status("alpha").state == "applied"

    with d.runtime("alpha").lease() as engine:
        assert [lane.channel for lane in engine._router._lanes.values()] == [
            Channel.WHATSAPP,
            Channel.WHATSAPP,
            Channel.EMAIL,
        ]
        approve_everything(engine)
        engine.dispatch()
    assert [m.prospect_id for m in d.channel("alpha", Channel.EMAIL).sent] == [crm]


def test_a_reconfiguration_that_cannot_be_built_is_refused_and_the_client_keeps_running(deploy):
    d = deploy({"alpha": raw_for("alpha")})
    before = d.runtime("alpha").engine
    d.failing_sources.update({"hubspot": "no access", "sheet_b": "bad sheet"})
    d.reconfigure("alpha", lanes=[{**LANES[0], "products": ["a"]}, *LANES[1:]])
    d.application.runtime_manager.refresh_client("alpha").result()
    status = d.application.runtime_manager.status("alpha")
    assert status.state == "failed"
    message = " ".join(status.errors)
    assert "source 'hubspot': no access" in message and "source 'sheet_b': bad sheet" in message
    assert d.runtime("alpha").engine is before  # still the Engine that worked


def test_a_reconfiguration_onto_another_clients_mailbox_is_refused(deploy):
    d = deploy({"alpha": raw_for("alpha"), "beta": other_client("beta", "sheet-beta")})
    d.reconfigure("beta", lanes=[{"id": "main", "source": "sheet_x", "channel": "email",
                                  "products": ["a"]}], channel=EMAIL_CHANNEL)  # fmt: skip
    d.application.runtime_manager.refresh_client("beta").result()
    status = d.application.runtime_manager.status("beta")
    assert (
        status.state == "failed" and "mailbox is already used by client 'alpha'" in status.errors[0]
    )


# ---- two clients never share an account --------------------------------------------------------


def test_two_clients_on_the_same_mailbox_or_spreadsheet_are_refused_together(deploy):
    clients = {
        "alpha": raw_for("alpha"),
        "beta": other_client("beta", "sheet-a", "email", mailbox=EMAIL_CHANNEL),  # the same ones
    }
    with pytest.raises(ConfigurationError) as raised:
        deploy(clients)
    errors = raised.value.errors
    assert any("email mailbox" in e and "alpha, beta" in e for e in errors), errors
    assert any("spreadsheet_id 'sheet-a'" in e and "alpha, beta" in e for e in errors), errors


def test_a_telegram_bot_shared_by_two_lane_clients_is_one_refusal_naming_both(deploy):
    secrets = secrets_for("alpha", "beta")
    secrets.set(client_scope("beta"), "telegram.bot_token", bot_token(1, "alpha"), by="test")
    clients = {"alpha": raw_for("alpha"), "beta": other_client("beta", "sheet-beta", "telegram")}
    with pytest.raises(ConfigurationError) as raised:
        deploy(clients, secrets)
    errors = [e for e in raised.value.errors if "Telegram bot" in e]
    assert len(errors) == 1 and "alpha, beta" in errors[0]
    assert "token-value" not in errors[0]  # the bot's id is not a secret, its token is


def test_a_client_never_runs_on_the_account_of_another(deploy):
    d = deploy({"alpha": raw_for("alpha"), "beta": other_client("beta", "sheet-beta")})
    registry = d.application.registry
    assert registry.by_routing_key("whatsapp", "phone-alpha").client_id == "alpha"
    assert registry.by_routing_key("whatsapp", "phone-beta").client_id == "beta"
    assert d.channel("alpha", Channel.WHATSAPP) is not d.channel("beta", Channel.WHATSAPP)
    assert ("beta", Channel.TELEGRAM) not in d.channels  # beta runs no Telegram lane


# ---- a client starts only if its configuration is valid ----------------------------------------


def test_every_missing_credential_of_every_lane_is_reported_together(deploy):
    d = deploy(
        {"alpha": raw_for("alpha"), "beta": other_client("beta", "sheet-beta")},
        secrets_for("beta", email=False, telegram=False, hubspot=False),
    )
    alpha = d.application.registry.get_config("alpha")
    assert not alpha.ready
    assert set(alpha.missing_credentials) >= {
        "HubSpot access token is not configured",
        "Email password is not configured",
        "Telegram bot token is not configured",
        "Telegram webhook secret is not configured",
    }
    health = {entry["client_id"]: entry for entry in d.application.registry.health()}
    assert health["alpha"]["status"] == "not_ready"
    assert health["beta"]["status"] == "ready"  # one client's gaps never stop another
    assert d.application.registry.by_routing_key("whatsapp", "phone-alpha") is None
    assert d.application.registry.not_ready_client_for("whatsapp", "phone-alpha") == "alpha"


def test_incomplete_email_settings_make_the_client_not_ready(deploy):
    d = deploy({"alpha": raw_for("alpha", channel=None)})
    assert (
        "Email settings are incomplete"
        in d.application.registry.get_config("alpha").missing_credentials
    )


def test_what_building_reveals_is_reported_for_every_source_and_channel_at_once(deploy):
    d = deploy({"alpha": raw_for("alpha")})
    config = d.application.registry.get_config("alpha")
    d.failing_sources.update(
        {"hubspot": "the HubSpot access token is not configured", "sheet_b": "bad sheet"}
    )
    d.failing_channels.update({"telegram": "bad bot"})
    with pytest.raises(RuntimeBuildError) as raised:
        build_runtime(config, d.builders(), d.clock)
    message = str(raised.value)
    for part in ("source 'hubspot'", "source 'sheet_b'", "channel 'telegram'", "alpha"):
        assert part in message, message
    assert "source 'sheet_a'" not in message  # what works is not reported


def test_a_provider_that_cannot_be_run_is_refused_in_words(deploy):
    d = deploy({"alpha": raw_for("alpha")})
    config = d.application.registry.get_config("alpha")
    builders = replace(
        d.builders(),
        source_instances={"google_sheets": d.builders().source_instances["google_sheets"]},
    )
    with pytest.raises(RuntimeBuildError, match="source 'hubspot': 'hubspot' cannot be run yet"):
        build_runtime(config, builders, d.clock)


def test_a_deployment_without_the_operational_store_refuses_lanes(deploy):
    d = deploy({"alpha": raw_for("alpha")})
    config = d.application.registry.get_config("alpha")
    with pytest.raises(RuntimeBuildError, match="operational store"):
        build_runtime(config, replace(d.builders(), operational_stores=None), d.clock)


def test_a_lane_configuration_that_is_invalid_stops_the_start_and_says_why(tmp_path, sql_engine):
    lanes = [{**LANES[0], "products": ["a", "ghost"]}, *LANES[1:]]
    with pytest.raises(ConfigurationError) as raised:
        # written straight into client.yaml, as an operator could: nothing validated it before
        Deployment(
            tmp_path, sql_engine, {"alpha": raw_for("alpha", lanes=lanes)}, secrets_for("alpha"),
            publish=False,
        )  # fmt: skip
    errors = raised.value.errors
    assert any("lanes[0].products: 'ghost' is not a product of the catalog" in e for e in errors)


# ---- a client with one source and one channel is exactly what it was ---------------------------


def test_a_legacy_client_still_runs_beside_a_client_with_lanes(deploy):
    legacy = client_yaml("beta")
    d = deploy({"alpha": raw_for("alpha"), "beta": legacy})
    assert d.runtime("beta").engine._router.explicit is False
    assert d.runtime("alpha").engine._router.explicit is True
    assert d.runtime("alpha").engine._router.channels == (
        Channel.WHATSAPP,
        Channel.EMAIL,
        Channel.TELEGRAM,
    )


def test_the_builders_a_deployment_built_before_lanes_still_construct():
    builders, _ = fake_builders(FakeClock(NOW))
    assert (
        builders.source_instances == {}
    )  # a new field with a default: older callers are unchanged


def test_the_default_builders_know_how_to_build_each_source_instance_of_a_lane_client():
    from sales_app.settings import Settings

    builders = default_builders(Settings(clients_dir="."))
    assert set(builders.source_instances) == {"google_sheets", "hubspot"}
    assert HUBSPOT["status_mapping"]  # the settings the HubSpot instance is built from


def test_a_client_with_two_channels_to_poll_reads_each_inbox_and_acknowledges_where_it_found(
    deploy,
):
    d = deploy({"alpha": raw_for("alpha")})
    config = d.application.registry.get_config("alpha")
    first, second = Mailbox(), Mailbox()
    first.deliver(EMAIL, "one", "m1")
    second.deliver("other@example.org", "two", "m2")
    email = d.application.builders.channels["email"]
    telegram = d.application.builders.channels["telegram"]
    builders = replace(
        d.application.builders,
        channels={
            **d.application.builders.channels,
            "email": replace(email, build_inbox=lambda c: first),
            "telegram": replace(telegram, build_inbox=lambda c: second),
        },
    )
    poller = builders.open_inbox(config)
    assert [item.message.text for item in poller.fetch(10)] == ["one", "two"]
    for item in poller.fetch(10):
        poller.acknowledge(item.handle)
    poller.close()
    assert (first.acknowledged, second.acknowledged) == (["m1"], ["m2"])
    assert (first.closed, second.closed) == (1, 1)
