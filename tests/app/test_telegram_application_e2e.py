"""The whole application on Google Sheets with the Telegram channel, from the Admin's decision to
the reply that comes back through the webhook.

What is real: the `Application` (configuration, registry, scheduler, agent switch, webhook parsers),
the Admin and Client HTTP routes that authorize, set up and connect the channel, the `/webhooks/
telegram` route with its real parser, the credential store, the descriptor's own functions, the
Engine, the Brain package loading, and `GoogleSheetsRepository` reading and writing the tabs.

What is simulated, because it would need a real account: the Google Sheets API (an in-memory grid),
Telegram itself (`Bot`: it answers the Bot API's `sendMessage`, and plays the platform that echoes
back, in a header, the secret it was given when the bot was connected) and the model provider.
Nothing here proves that a real bot accepts the token, that Telegram delivers to the deployment's
address, or that a real person's Telegram app behaves like these updates.
"""

import json
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import httpx
import pytest
from app_support import InlineExecutor, env_for, fake_builders, make_settings, write_client
from factories import NOW
from fastapi.testclient import TestClient
from sheets_support import SID, TUNIS, FakeSheetsApi
from test_admin_clients_api import PASSWORD, owner
from test_client_agent_api import LOGIN, PASSWORDS, give_profile, mark_source_tested

from sales_app.admin_auth import InMemoryAdminSessionStore, InMemoryAdminUserStore
from sales_app.agent_state import InMemoryAgentStateStore
from sales_app.api import create_app
from sales_app.bootstrap import build_application
from sales_app.client_profiles import InMemoryClientProfileStore
from sales_app.config.secrets import TELEGRAM_BOT_TOKEN
from sales_app.config_store import InMemoryConfigStore
from sales_app.secret_store import InMemorySecretAudit, InMemorySecretStore
from sales_app.wiring import ChannelProvider, default_builders
from sales_app.wiring.providers import telegram as telegram_provider
from sales_core.contracts import Channel, ClientId
from sales_integrations.fakes import FakeClock
from sales_integrations.google_sheets import GoogleSheetsRepository
from sales_integrations.telegram import SECRET_HEADER, TelegramApi, TelegramChannel

BRAIN = "app_test_brains:follow_up_brain"
PHONE = "+21620000001"
PUBLIC = "https://revansa.example"
ENGAGE = {
    "spontaneous_conversations": {"enabled": True},
    "unknown_contacts": {"policy": "engage"},
}


def token_of(index: int, cid: str) -> str:
    return f"{1000000 + index}:AAH-{cid}-token-value-of-the-bot-padding-xx"


class Bot:
    """Telegram's side of one client's bot: the Bot API's `sendMessage`, and nothing else."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.sent: list[dict] = []
        self.wrong_token_calls = 0
        self.respond = None  # a function (request, body) -> httpx.Response, to make it misbehave
        self._ids = 500

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if f"/bot{self.token}/" not in str(request.url):
            self.wrong_token_calls += 1  # another client's bot would have answered, not this one
            return httpx.Response(401, json={"ok": False, "error_code": 401})
        body = json.loads(request.content)
        if self.respond is not None:
            return self.respond(request, body)
        self._ids += 1
        self.sent.append(body)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": self._ids}})

    def to(self, chat) -> list[dict]:
        return [m for m in self.sent if str(m["chat_id"]) == str(chat)]


class Telegram:
    """The application, its sheets and the bots of its clients. `restart()` keeps all of them."""

    def __init__(self, tmp_path, *client_ids, whatsapp=(), modes=None, inbound=None):
        self.root = tmp_path
        self.client_ids = (*client_ids, *whatsapp)
        self.clock = FakeClock(NOW)  # 10:00 on Thursday 15 January 2026, in Tunis
        self.sheets = {cid: FakeSheetsApi() for cid in self.client_ids}
        for api in self.sheets.values():
            api.create_layout()
        self.tokens = {cid: token_of(i, cid) for i, cid in enumerate(self.client_ids)}
        self.bots = {cid: Bot(self.tokens[cid]) for cid in self.client_ids}
        self.registered: dict[str, tuple[str, str]] = {}  # token -> (url, secret) Telegram holds
        for cid in self.client_ids:
            extra = {"mode": modes[cid]} if modes and cid in modes else {}
            if inbound and cid in inbound:
                extra["inbound"] = inbound[cid]
            write_client(
                tmp_path, cid, brain={"factory": BRAIN, "settings": {"tone": cid}}, **extra
            )
        builders, self.made = fake_builders(self.clock)
        self.builders = replace(
            builders,
            repositories={"google_sheets": self._repository},
            channels={**builders.channels, "telegram": self._telegram_provider()},
        )
        self.users = InMemoryAdminUserStore()
        self.users.create(owner())
        self.agent_state = InMemoryAgentStateStore()
        self.config_store = InMemoryConfigStore()
        self.secrets = InMemorySecretStore()
        self.audit = InMemorySecretAudit()
        self._profile_store = InMemoryClientProfileStore()
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

    def _api_for(self, config) -> TelegramApi:
        token = config.secrets.get(TELEGRAM_BOT_TOKEN)
        cid = config.client_id
        assert token == self.tokens[cid], "a client was given a bot that is not its own"
        return TelegramApi(token, client=self.bots[cid].http())

    def _telegram_provider(self):
        def build(config):
            return TelegramChannel(api=self._api_for(config), token="")

        def answer(config, message):
            telegram_provider.answer_unknown(config, message, api=self._api_for(config))

        return ChannelProvider(
            kind=Channel.TELEGRAM,
            build=build,
            # The descriptor's own functions: the bot's id and the secret come from the config.
            routing_key=telegram_provider.routing_key,
            webhook_secret=telegram_provider.webhook_secret,
            build_parser=lambda secrets: telegram_provider.build_parser(secrets, None),
            answer_unknown=answer,
        )

    def probe(self, token):
        """What the Client API asks Telegram about a bot: it knows the token it was given."""
        room = self

        class _Probe:
            def check_bot(self):
                assert token in room.tokens.values()
                return "naga_bot"

            def register_webhook(self, url, secret):
                room.registered[token] = (url, secret)

            def webhook_state(self, expected_url):
                return "ok" if room.registered.get(token, ("",))[0] == expected_url else "not_set"

        return _Probe()

    # ---- the application -------------------------------------------------------------------

    def start(self):
        settings = make_settings(self.root).model_copy(update={"public_base_url": PUBLIC})
        self.application = build_application(
            settings,
            env=env_for(*self.client_ids),
            builders=self.builders,
            clock=self.clock,
            executor=InlineExecutor(),
            admin_user_store=self.users,
            admin_session_store=InMemoryAdminSessionStore(),
            client_profile_store=self._profile_store,
            client_session_store=InMemoryAdminSessionStore(),
            config_store=self.config_store,
            agent_state_store=self.agent_state,
            secret_store=self.secrets,
            secret_audit=self.audit,
            refresh_executor=InlineExecutor(),
        )

    def restart(self):
        self.application.close()
        self.start()

    def close(self):
        self.application.close()

    def http(self):
        app = create_app(self.application, run_scheduler=False, bot_probe=self.probe)
        return TestClient(app, base_url="https://testserver")

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

    def allow_telegram(self, *, only_for=None):
        admin = self.admin()
        if only_for is None:
            response = admin.put(
                "/admin/v1/settings/channels", json={"allowed": ["whatsapp", "telegram"]}
            )
        else:
            response = admin.put(
                f"/admin/v1/clients/{only_for}/channels", json={"added": ["telegram"]}
            )
        assert response.status_code == 200, response.text

    def set_up_telegram(self, cid, *, start=True, connect=True):
        """What a client does: choose the channel, give its bot, connect it, start the agent."""
        browser = self.browser(cid)
        version = browser.get("/client/v1/channel").json()["version"]
        response = browser.put(
            "/client/v1/channel", json={"expected_version": version, "channel": "telegram"}
        )
        assert response.status_code == 200, response.text
        response = browser.put(
            "/client/v1/channel/credentials", json={"bot_token": self.tokens[cid]}
        )
        assert response.status_code == 200, response.text
        if connect:
            response = browser.post("/client/v1/channel/connect")
            assert response.status_code == 200 and response.json()["ok"], response.text
        if start:
            response = browser.post("/client/v1/agent/start")
            assert response.status_code == 200, response.text
            assert response.json()["active"] is True
        return browser

    def add_prospect(self, cid, pid="p1", **cells):
        values = {
            "prospect_id": pid,
            "display_name": "Alex",
            "phone": PHONE,
            "status": "new",
        }
        self.sheets[cid].human_add_row("Prospects", **(values | cells))

    def approve(self, cid, pid="p1", nth=-1):
        api = self.sheets[cid]
        rows = [i for i, r in enumerate(api.tabs["Actions"][1:], start=2) if pid in r]
        api.human_set("Actions", rows[nth], "status", "approved")

    # ---- what Telegram does ----------------------------------------------------------------

    def secret_of(self, cid):
        """The secret Telegram echoes for this client's bot: the one it was given at connection."""
        return self.registered[self.tokens[cid]][1]

    def update(self, chat, *, mid, **message):
        return {
            "update_id": 7000 + mid,
            "message": {
                "message_id": mid,
                "date": int(self.clock.now().timestamp()),
                "chat": {"id": int(chat), "type": "private", "first_name": "Sam"},
                "from": {"id": int(chat), "is_bot": False, "first_name": "Sam"},
            }
            | message,
        }

    def deliver(self, body, cid=None, *, secret="echo", client=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if secret == "echo":
            secret = self.secret_of(cid)
        if secret is not None:
            headers[SECRET_HEADER] = secret
        return (client or self.http()).post("/webhooks/telegram", content=raw, headers=headers)

    def says(self, cid, chat, text, *, mid=1, **kw):
        return self.deliver(self.update(chat, mid=mid, text=text), cid, **kw)

    def starts(self, cid, chat, payload=None, *, mid=1, **kw):
        text = "/start" if payload is None else f"/start {payload}"
        return self.deliver(self.update(chat, mid=mid, text=text), cid, **kw)

    def shares(self, cid, chat, phone, *, mid=1, owner=True, **kw):
        contact = {"phone_number": phone, "first_name": "Sam", "user_id": chat if owner else 1}
        return self.deliver(self.update(chat, mid=mid, contact=contact), cid, **kw)

    def invitation(self, cid, pid="p1"):
        """The payload of the link the client would send this prospect."""
        body = self.browser(cid).get("/client/v1/channel/invitations").json()
        link = next(i["link"] for i in body["invitations"] if i["prospect_id"] == pid)
        assert link.startswith("https://t.me/naga_bot?start=")
        return link.split("start=", 1)[1]

    # ---- time and work ---------------------------------------------------------------------

    def tick(self, advance=timedelta(minutes=10)):
        self.clock.advance(advance)
        futures = self.application.scheduler.tick(self.clock.now())
        return [result for future in futures for result in future.result()]

    # ---- what the sheets and the bots hold -------------------------------------------------

    def actions(self, cid):
        return self.sheets[cid].rows("Actions")

    def messages(self, cid, direction=None):
        rows = self.sheets[cid].rows("Messages")
        return [r for r in rows if direction is None or r["direction"] == direction]

    def prospect(self, cid, pid="p1"):
        api = self.sheets[cid]
        row = api.find_row("Prospects", "prospect_id", pid)
        return {h: api.cell("Prospects", row, h) for h in api.headers("Prospects")}

    def sent(self, cid):
        return self.bots[cid].sent


@pytest.fixture
def room(tmp_path):
    made = Telegram(tmp_path, "alpha")
    made.allow_telegram()
    made.set_up_telegram("alpha")
    yield made
    made.close()


@pytest.fixture
def pair(tmp_path):
    made = Telegram(tmp_path, "alpha", "beta")
    made.allow_telegram()
    made.set_up_telegram("alpha")
    made.set_up_telegram("beta")
    yield made
    made.close()


CHAT = 424242
OTHER_CHAT = 515151


# ---- the path: sheet -> invitation -> start -> proposal -> approval -> Telegram, and back -----------


def test_the_whole_path_from_the_sheet_to_telegram_and_back(room):
    r = room
    r.add_prospect("alpha")

    # The prospect is in the sheet but has never spoken to the bot: nothing can be sent, and
    # nothing is spoilt (no action, no pause, no quota spent).
    assert all(x.ok for x in r.tick())
    assert r.actions("alpha") == [] and r.sent("alpha") == []
    assert r.prospect("alpha")["status"] == "new"

    # The client sends them their link; they press Start.
    payload = r.invitation("alpha")
    response = r.starts("alpha", CHAT, payload)
    assert response.status_code == 200 and response.json() == {"received": 1}
    assert r.prospect("alpha")["telegram_chat_id"] == str(CHAT)
    assert r.prospect("alpha")["phone"] == PHONE  # what the client typed is untouched
    (start,) = r.messages("alpha", "inbound")
    assert start["text"] == "" and start["external_id"] == f"{CHAT}:1"
    (reply,) = r.actions("alpha")
    assert reply["action_type"] == "reply" and reply["status"] == "proposed"
    assert r.sent("alpha") == []  # approval mode: nothing leaves before a person says so

    # The team approves; the bot answers in the person's chat.
    r.approve("alpha")
    assert all(x.ok for x in r.tick())
    (sent,) = r.sent("alpha")
    assert sent == {"chat_id": str(CHAT), "text": "[alpha] hello Alex"}
    (outbound,) = r.messages("alpha", "outbound")
    assert outbound["channel"] == "telegram" and outbound["external_id"].startswith(f"{CHAT}:")
    assert r.prospect("alpha")["status"] == "active"
    assert r.prospect("alpha")["next_action_type"] == "follow_up"

    # They answer; the answer is stored and answered in turn.
    assert r.says("alpha", CHAT, "Yes, tell me more.", mid=2).json() == {"received": 1}
    (_, inbound) = r.messages("alpha", "inbound")
    assert inbound["text"] == "Yes, tell me more." and inbound["channel"] == "telegram"
    assert r.prospect("alpha")["last_inbound_at"]
    r.approve("alpha")
    r.tick()
    assert len(r.sent("alpha")) == 2
    assert [m["direction"] for m in r.messages("alpha")] == [
        "inbound",
        "outbound",
        "inbound",
        "outbound",
    ]

    # Days later the follow-up the Brain asked for is due, on the same chat.
    r.tick(timedelta(days=4))
    follow_up = [a for a in r.actions("alpha") if a["action_type"] == "follow_up"]
    assert len(follow_up) == 1 and follow_up[0]["status"] == "proposed"
    r.approve("alpha", nth=-1)
    r.tick()
    assert r.bots["alpha"].to(CHAT) == r.sent("alpha") and len(r.sent("alpha")) == 3


def test_a_chat_id_typed_in_the_sheet_is_not_proof_that_the_person_started_the_bot(room):
    """Regression: a legacy client keeps the link in its sheet, so anyone able to edit the sheet
    could write a chat id and make Revansa speak first. Only a message the person sent counts."""
    r = room
    r.add_prospect("alpha", telegram_chat_id=str(CHAT))
    for _ in range(3):
        assert all(x.ok for x in r.tick())
        if r.actions("alpha"):  # even if a person approves whatever was planned
            r.approve("alpha")
    assert r.sent("alpha") == [] and r.bots["alpha"].to(CHAT) == []
    assert not [a for a in r.actions("alpha") if a["status"] == "sent"]
    assert r.prospect("alpha")["status"] == "new"  # waiting, not paused or lost

    # The person really writes: that message is the proof, and the conversation can go on.
    assert r.says("alpha", CHAT, "Hello, I am interested.", mid=1).json() == {"received": 1}
    r.approve("alpha")
    assert all(x.ok for x in r.tick())
    assert [m["chat_id"] for m in r.sent("alpha")] == [str(CHAT)]


def test_a_prospect_who_has_not_started_the_bot_waits_and_is_never_lost(room):
    r = room
    r.add_prospect("alpha", "p1")
    r.add_prospect("alpha", "p2", display_name="Sam", phone="+21620000002")
    for _ in range(4):  # four days (to a Monday): a quota is spent on nobody, nobody is paused
        r.tick(timedelta(days=1))
    assert r.actions("alpha") == [] and r.sent("alpha") == []
    assert r.prospect("alpha", "p1")["status"] == "new"
    assert r.prospect("alpha", "p2")["status"] == "new"
    # p2 says hello days later: from then on, they are a normal prospect
    r.starts("alpha", OTHER_CHAT, r.invitation("alpha", "p2"))
    r.approve("alpha", "p2")
    r.tick()
    assert [m["chat_id"] for m in r.sent("alpha")] == [str(OTHER_CHAT)]
    assert r.prospect("alpha", "p1")["telegram_chat_id"] == ""  # p1 still waits


def test_the_client_sees_who_is_waiting_and_how_to_invite_them(room):
    r = room
    r.add_prospect("alpha", "p1", email="alex@example.com")
    r.add_prospect("alpha", "p2", display_name="Sam", phone="+21620000002")
    browser = r.browser("alpha")
    body = browser.get("/client/v1/channel/invitations").json()
    assert body["bot_username"] == "naga_bot" and body["bot_link"] == "https://t.me/naga_bot"
    assert body["waiting"] == 2
    first = next(i for i in body["invitations"] if i["prospect_id"] == "p1")
    assert first["display_name"] == "Alex" and first["email"] == "alex@example.com"
    assert first["phone"] == PHONE and first["link"].startswith("https://t.me/naga_bot?start=")
    assert len({i["link"] for i in body["invitations"]}) == 2  # one for each person

    r.starts("alpha", CHAT, r.invitation("alpha", "p1"))
    body = browser.get("/client/v1/channel/invitations").json()
    assert body["waiting"] == 1 and [i["prospect_id"] for i in body["invitations"]] == ["p2"]
    assert browser.get("/client/v1/channel/invitations?limit=1").json()["invitations"] != []


def test_an_opt_out_on_telegram_stops_everything(room):
    r = room
    r.add_prospect("alpha")
    r.starts("alpha", CHAT, r.invitation("alpha"))
    r.approve("alpha")
    r.tick()
    assert len(r.sent("alpha")) == 1
    r.says("alpha", CHAT, "STOP", mid=2)
    assert r.prospect("alpha")["status"] == "opted_out"
    r.tick(timedelta(days=4))
    r.tick(timedelta(days=4))
    assert len(r.sent("alpha")) == 1


def test_an_invitation_forwarded_to_someone_else_takes_nothing_over(room):
    r = room
    r.add_prospect("alpha")
    payload = r.invitation("alpha")
    r.starts("alpha", CHAT, payload)
    r.starts("alpha", OTHER_CHAT, payload, mid=2)  # the link, forwarded
    assert r.prospect("alpha")["telegram_chat_id"] == str(CHAT)
    assert [m["external_id"] for m in r.messages("alpha", "inbound")] == [f"{CHAT}:1"]
    # the second person is asked who they are, like any stranger
    assert r.bots["alpha"].to(OTHER_CHAT)[0]["reply_markup"]["keyboard"][0][0]["request_contact"]


# ---- people who write first ---------------------------------------------------------------------


def test_a_stranger_is_asked_to_share_their_number_and_is_then_recognised(room):
    r = room
    r.add_prospect("alpha")
    assert r.says("alpha", CHAT, "Hello?").json() == {"received": 1}
    assert r.messages("alpha") == [] and r.actions("alpha") == []  # nothing stored, nobody created
    (prompt,) = r.bots["alpha"].to(CHAT)
    assert "share your phone number" in prompt["text"]
    assert prompt["reply_markup"]["keyboard"] == [
        [{"text": "Share my phone number", "request_contact": True}]
    ]

    r.shares("alpha", CHAT, "21620000001", mid=2)  # no "+": Telegram does not always send one
    assert r.prospect("alpha")["telegram_chat_id"] == str(CHAT)
    (reply,) = r.actions("alpha")
    assert reply["action_type"] == "reply" and reply["status"] == "proposed"
    assert len(r.bots["alpha"].to(CHAT)) == 1  # the person is not asked again


def test_a_number_nobody_holds_gets_an_honest_answer_and_nothing_else(room):
    r = room
    r.add_prospect("alpha")
    r.shares("alpha", CHAT, "+21699999999")
    (answer,) = r.bots["alpha"].to(CHAT)
    assert "could not match" in answer["text"] and answer["reply_markup"] == {
        "remove_keyboard": True
    }
    assert r.prospect("alpha")["telegram_chat_id"] == "" and r.actions("alpha") == []


def test_somebody_elses_number_proves_nothing(room):
    r = room
    r.add_prospect("alpha")
    r.shares("alpha", CHAT, "21620000001", owner=False)  # a contact card, not their own number
    assert r.prospect("alpha")["telegram_chat_id"] == ""
    assert r.actions("alpha") == []


def test_typing_a_phone_number_is_not_proof_either(room):
    r = room
    r.add_prospect("alpha")
    r.says("alpha", CHAT, "my number is +21620000001")
    assert r.prospect("alpha")["telegram_chat_id"] == ""


def test_the_words_the_bot_uses_are_the_clients_own(tmp_path):
    r = Telegram(tmp_path, "alpha")
    try:
        r.allow_telegram()
        browser = r.set_up_telegram("alpha")
        version = browser.get("/client/v1/channel").json()["version"]
        browser.put(
            "/client/v1/channel",
            json={
                "expected_version": version,
                "channel": "telegram",
                "telegram": {
                    "contact_prompt": "Bonjour ! Partagez votre numéro.",
                    "contact_button": "Partager",
                },
            },
        )
        r.says("alpha", CHAT, "Salut")
        (prompt,) = r.bots["alpha"].to(CHAT)
        assert prompt["text"] == "Bonjour ! Partagez votre numéro."
        assert prompt["reply_markup"]["keyboard"][0][0]["text"] == "Partager"
    finally:
        r.close()


def test_a_client_that_engages_strangers_answers_them_and_keeps_them(tmp_path):
    r = Telegram(tmp_path, "alpha", inbound={"alpha": ENGAGE})
    try:
        r.allow_telegram()
        r.set_up_telegram("alpha")
        assert r.says("alpha", CHAT, "Is the offer still open?").json() == {"received": 1}
        (created,) = r.sheets["alpha"].rows("Prospects")
        assert created["prospect_id"] == f"inbound-2026-01-15-tg{CHAT}"
        assert created["telegram_chat_id"] == str(CHAT) and created["phone"] == ""
        assert created["display_name"] == "Sam" and created["status"] == "active"
        (inbound,) = r.messages("alpha", "inbound")
        assert inbound["text"] == "Is the offer still open?"
        (reply,) = r.actions("alpha")
        assert reply["action_type"] == "reply" and reply["status"] == "proposed"
        assert r.sent("alpha") == []  # no prompt: they are a conversation already

        r.approve("alpha", created["prospect_id"])
        r.tick()
        assert r.bots["alpha"].to(CHAT) == [{"chat_id": str(CHAT), "text": "[alpha] hello Sam"}]
        r.says("alpha", CHAT, "Thanks", mid=2)
        assert len(r.sheets["alpha"].rows("Prospects")) == 1  # the same person next time
    finally:
        r.close()


def test_a_known_prospect_who_writes_first_without_a_link_is_asked_not_guessed(room):
    r = room
    r.add_prospect("alpha", display_name="Chat")
    r.says("alpha", CHAT, "I'm Chat, I got your offer")  # a name is not an identity
    assert r.prospect("alpha")["telegram_chat_id"] == ""
    assert len(r.bots["alpha"].to(CHAT)) == 1


# ---- what the webhook accepts -------------------------------------------------------------------


class TestTheWebhook:
    def test_a_request_without_the_secret_is_refused_and_nothing_is_read(self, room):
        room.add_prospect("alpha")
        for secret in (None, "", "wrong", room.secret_of("alpha") + "x"):
            assert room.says("alpha", CHAT, "hi", secret=secret).status_code == 401
        assert room.messages("alpha") == [] and room.sent("alpha") == []

    def test_a_secret_that_belongs_to_no_client_is_refused_even_with_a_valid_body(self, room):
        response = room.says("alpha", CHAT, "hi", secret="s" * 43)
        assert response.status_code == 401

    def test_garbage_with_the_right_secret_is_a_bad_request_not_a_crash(self, room):
        for body in (b"not json", b"[]", b'{"message": {}}'):
            assert room.deliver(body, "alpha").status_code == 400
        assert room.messages("alpha") == []

    def test_what_is_not_a_conversation_is_acknowledged_and_ignored(self, room):
        room.add_prospect("alpha")
        group = room.update(CHAT, mid=1, text="hi") | {}
        group["message"]["chat"] = {"id": -100123, "type": "group"}
        assert room.deliver(group, "alpha").json() == {"received": 0}
        edited = {"update_id": 5, "edited_message": room.update(CHAT, mid=1, text="x")["message"]}
        assert room.deliver(edited, "alpha").json() == {"received": 0}
        assert room.messages("alpha") == [] and room.sent("alpha") == []

    def test_an_update_delivered_twice_is_stored_and_answered_once(self, room):
        r = room
        r.add_prospect("alpha")
        r.starts("alpha", CHAT, r.invitation("alpha"))
        r.approve("alpha")
        r.tick()
        update = r.update(CHAT, mid=2, text="Tell me more")
        r.deliver(update, "alpha")
        r.deliver(update, "alpha")  # Telegram retries when it did not hear back in time
        assert len(r.messages("alpha", "inbound")) == 2  # the start and this one, once each
        assert len([a for a in r.actions("alpha") if a["action_type"] == "reply"]) == 2

    def test_a_get_is_not_a_handshake(self, room):
        assert room.http().get("/webhooks/telegram").status_code == 403

    def test_the_webhook_of_a_channel_nobody_uses_is_not_there(self, tmp_path):
        r = Telegram(tmp_path, "alpha")
        try:
            assert r.http().post("/webhooks/telegram", content=b"{}").status_code == 404
        finally:
            r.close()


# ---- isolation between clients ------------------------------------------------------------------


class TestIsolation:
    def test_a_message_goes_to_the_client_whose_bot_received_it(self, pair):
        r = pair
        for cid in ("alpha", "beta"):
            r.add_prospect(cid)  # the same prospect, id and phone, in two spreadsheets
        r.starts("alpha", CHAT, r.invitation("alpha"))
        assert r.prospect("alpha")["telegram_chat_id"] == str(CHAT)
        assert r.prospect("beta")["telegram_chat_id"] == "" and r.messages("beta") == []

    def test_each_client_answers_through_its_own_bot_only(self, pair):
        r = pair
        for cid in ("alpha", "beta"):
            r.add_prospect(cid)
            r.starts(cid, CHAT, r.invitation(cid))  # the very same person, in both
        r.approve("alpha")
        r.tick()
        assert len(r.sent("alpha")) == 1 and r.sent("beta") == []
        r.approve("beta")
        r.tick()
        assert len(r.sent("beta")) == 1 and len(r.sent("alpha")) == 1
        assert all(bot.wrong_token_calls == 0 for bot in r.bots.values())

    def test_one_clients_secret_does_not_open_anothers_conversations(self, pair):
        r = pair
        r.add_prospect("beta")
        r.starts("beta", CHAT, r.invitation("beta"))
        r.says("alpha", CHAT, "I am writing to alpha's bot")  # a stranger there
        assert [m["text"] for m in r.messages("beta", "inbound")] == [""]
        assert r.messages("alpha") == []

    def test_an_invitation_made_for_one_client_links_nobody_at_another(self, pair):
        r = pair
        for cid in ("alpha", "beta"):
            r.add_prospect(cid)
        payload = r.invitation("alpha")
        r.starts("beta", CHAT, payload)  # alpha's link, pressed on beta's bot
        assert r.prospect("beta")["telegram_chat_id"] == ""
        assert r.prospect("alpha")["telegram_chat_id"] == ""

    def test_one_clients_opt_out_is_not_the_others(self, pair):
        r = pair
        for cid in ("alpha", "beta"):
            r.add_prospect(cid)
            r.starts(cid, CHAT, r.invitation(cid))
        r.says("alpha", CHAT, "STOP", mid=2)
        assert r.prospect("alpha")["status"] == "opted_out"
        assert r.prospect("beta")["status"] == "active"  # still in conversation with beta

    def test_the_two_clients_secrets_are_not_the_same(self, pair):
        assert pair.secret_of("alpha") != pair.secret_of("beta")

    def test_a_second_client_cannot_connect_the_bot_of_the_first(self, pair):
        r = pair
        r.tokens["beta"] = r.tokens["alpha"]  # beta pastes alpha's token
        beta = r.browser("beta")
        assert beta.put("/client/v1/channel/credentials", json={"bot_token": r.tokens["alpha"]})
        response = beta.post("/client/v1/channel/connect")
        assert response.status_code == 409 and "alpha" not in response.text

    def test_a_second_client_cannot_take_over_a_bot_at_runtime(self, pair):
        r = pair
        from sales_app.api.admin_clients import publish_client_settings

        r.secrets.set(  # an administrator stores alpha's bot for beta
            "client:beta", "telegram.bot_token", r.tokens["alpha"], by="root", unique=False
        )
        version = r.config_store.current("beta").version
        publish_client_settings(
            r.application,
            "beta",
            {"channel": {"provider": "telegram"}},
            expected_version=version,
            author="root",
            top_keys=(),
            sections={"channel": ("provider", "settings")},
        )
        r.application.runtime_manager.refresh_client("beta").result()
        status = r.application.runtime_manager.status("beta")
        assert status.state == "failed" and "already used" in " ".join(status.errors)
        r.add_prospect("alpha")
        r.starts("alpha", CHAT, r.invitation("alpha"))
        assert len(r.messages("alpha", "inbound")) == 1  # alpha is not disturbed
        assert r.says("beta", OTHER_CHAT, "still my bot", mid=5).status_code == 200  # nor is beta
        assert r.messages("beta") == [] and r.bots["beta"].to(OTHER_CHAT)  # beta's own bot answered


def test_a_client_on_whatsapp_keeps_its_webhook_while_another_uses_telegram(tmp_path):
    from test_app_api import payload, post

    r = Telegram(tmp_path, "alpha", whatsapp=("beta",))
    try:
        r.allow_telegram()
        r.set_up_telegram("alpha")
        r.application.agent_state.set("beta", running=True, by="t", at=r.clock.now())
        r.add_prospect("beta", phone="+15550000001")
        client = r.http()
        body = payload("phone-beta", "15550000001", "Hello from WhatsApp", "wamid.9")
        response = post(client, body, secret="secret-beta")
        assert response.status_code == 200 and response.json() == {"received": 1}
        assert [m["text"] for m in r.messages("beta", "inbound")] == ["Hello from WhatsApp"]
        assert r.messages("alpha") == []
        assert set(r.application.parsers) == {"whatsapp", "telegram"}
    finally:
        r.close()


# ---- the Admin's authority and the agent's switch ------------------------------------------------


class TestAuthority:
    def test_a_channel_the_admin_takes_away_stops_working_at_once(self, room):
        r = room
        r.add_prospect("alpha")
        r.starts("alpha", CHAT, r.invitation("alpha"))
        r.approve("alpha")  # approved, about to be sent
        response = r.admin().put("/admin/v1/clients/alpha/channels", json={"removed": ["telegram"]})
        assert response.status_code == 200
        assert any("not authorized" in m for m in r.application.registry.missing("alpha"))

        assert r.tick() == [] and r.tick(timedelta(hours=3)) == []
        assert r.sent("alpha") == []
        # the person writes meanwhile: Telegram is answered 200 (no retry can help), nothing is kept
        assert r.says("alpha", CHAT, "Hello?", mid=2).status_code == 200
        assert len(r.messages("alpha", "inbound")) == 1  # only what was stored before
        assert r.sent("alpha") == []

    def test_the_channel_works_again_when_the_admin_gives_it_back(self, room):
        r = room
        r.add_prospect("alpha")
        r.starts("alpha", CHAT, r.invitation("alpha"))
        r.approve("alpha")
        admin = r.admin()
        admin.put("/admin/v1/clients/alpha/channels", json={"removed": ["telegram"]})
        assert r.tick() == [] and r.sent("alpha") == []
        admin.put("/admin/v1/clients/alpha/channels", json={})
        r.tick()
        assert len(r.sent("alpha")) == 1

    def test_removing_the_token_stops_the_client_without_a_secret_in_the_report(self, room):
        r = room
        r.browser("alpha").delete("/client/v1/channel/credentials")
        missing = " ".join(r.application.registry.missing("alpha"))
        assert missing and r.tokens["alpha"] not in missing
        assert r.tick() == []

    def test_a_paused_agent_keeps_what_arrives_and_does_not_answer_until_restarted(self, room):
        r = room
        r.add_prospect("alpha")
        payload = r.invitation("alpha")
        r.browser("alpha").post("/client/v1/agent/pause")
        r.starts("alpha", CHAT, payload)
        r.says("alpha", OTHER_CHAT, "stranger", mid=3)
        assert r.actions("alpha") == [] and r.sent("alpha") == []  # stored, not answered, not asked
        assert len(r.messages("alpha", "inbound")) == 1
        r.browser("alpha").post("/client/v1/agent/start")
        r.tick()
        assert [a["action_type"] for a in r.actions("alpha")] == ["reply"]

    def test_a_dry_run_never_talks_to_telegram(self, tmp_path):
        r = Telegram(tmp_path, "alpha", modes={"alpha": "dry_run"})
        try:
            r.allow_telegram()
            r.set_up_telegram("alpha", start=False)
            r.application.agent_state.set("alpha", running=True, by="t", at=r.clock.now())
            r.add_prospect("alpha")
            r.says("alpha", CHAT, "Hello?")
            r.tick()
            r.tick(timedelta(hours=1))
            assert r.sent("alpha") == [] and r.messages("alpha") == []
        finally:
            r.close()


# ---- failures -----------------------------------------------------------------------------------


class TestWhenTelegramMisbehaves:
    def linked_and_approved(self, r):
        r.add_prospect("alpha")
        r.starts("alpha", CHAT, r.invitation("alpha"))
        r.approve("alpha")

    def test_a_person_who_blocked_the_bot_is_paused_not_retried(self, room):
        r = room
        self.linked_and_approved(r)
        r.bots["alpha"].respond = lambda request, body: httpx.Response(
            403,
            json={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked"},
        )
        r.tick()
        assert r.actions("alpha")[0]["status"] == "failed"
        assert r.prospect("alpha")["status"] == "paused"
        r.bots["alpha"].respond = None
        r.tick(timedelta(days=2))
        assert r.sent("alpha") == []

    def test_a_rate_limit_is_tried_again_later_and_the_message_arrives_once(self, room):
        r = room
        self.linked_and_approved(r)
        r.bots["alpha"].respond = lambda request, body: httpx.Response(
            429,
            json={"ok": False, "error_code": 429, "parameters": {"retry_after": 3}},
        )
        r.tick()
        assert r.sent("alpha") == [] and r.actions("alpha")[0]["status"] != "sent"
        r.bots["alpha"].respond = None
        for _ in range(4):
            r.tick(timedelta(hours=1))
        assert len(r.sent("alpha")) == 1

    def test_an_answer_that_never_came_is_never_sent_twice(self, room):
        r = room
        self.linked_and_approved(r)

        def hangs(request, body):
            raise httpx.ReadTimeout("no answer")

        r.bots["alpha"].respond = hangs
        r.tick()
        assert r.actions("alpha")[0]["status"] == "sending"  # left for a human to reconcile
        r.bots["alpha"].respond = None
        for _ in range(4):
            r.tick(timedelta(hours=1))
        assert r.sent("alpha") == []  # the Engine does not resend what may already have arrived

    def test_a_broken_token_does_not_pause_the_person_it_is_not_their_fault(self, room):
        r = room
        self.linked_and_approved(r)
        r.bots["alpha"].respond = lambda request, body: httpx.Response(
            401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
        )
        r.tick()
        assert r.prospect("alpha")["status"] != "paused"
        r.bots["alpha"].respond = None
        for _ in range(4):
            r.tick(timedelta(hours=1))
        assert len(r.sent("alpha")) == 1

    def test_the_sheet_refusing_a_write_makes_telegram_retry_and_loses_nothing(self, room):
        r = room
        r.add_prospect("alpha")
        payload = r.invitation("alpha")
        r.sheets["alpha"].fail_next("write", times=1)  # the link cannot be written
        failed = r.starts("alpha", CHAT, payload)
        assert failed.status_code == 500  # so that Telegram redelivers
        assert r.prospect("alpha")["telegram_chat_id"] == ""
        assert r.starts("alpha", CHAT, payload).status_code == 200  # the redelivery
        assert r.prospect("alpha")["telegram_chat_id"] == str(CHAT)
        assert len(r.messages("alpha", "inbound")) == 1


# ---- restart ------------------------------------------------------------------------------------


def test_a_restart_changes_nothing_the_link_lives_in_the_sheet(room):
    r = room
    r.add_prospect("alpha")
    r.starts("alpha", CHAT, r.invitation("alpha"))
    r.approve("alpha")
    r.restart()
    r.tick()
    assert len(r.sent("alpha")) == 1

    r.restart()
    r.says("alpha", CHAT, "Still here", mid=2)  # found by chat, with nothing in memory
    assert len(r.messages("alpha", "inbound")) == 2
    r.restart()
    r.tick()
    r.tick(timedelta(hours=2))
    assert len(r.sent("alpha")) == 1
    assert len([a for a in r.actions("alpha") if a["action_type"] == "reply"]) == 2


def test_a_reply_that_arrived_during_a_restart_is_still_answered(room):
    r = room
    r.add_prospect("alpha")
    r.starts("alpha", CHAT, r.invitation("alpha"))
    r.application.agent_state.set("alpha", running=False, by="t", at=r.clock.now())
    r.says("alpha", CHAT, "Anyone there?", mid=2)  # stored while the agent was off
    r.application.agent_state.set("alpha", running=True, by="t", at=r.clock.now())
    r.restart()
    r.tick()
    assert len([a for a in r.actions("alpha") if a["action_type"] == "reply"]) >= 1


# ---- the wiring the application really uses -----------------------------------------------------


def test_the_default_builders_make_telegram_a_channel_with_a_webhook_and_no_inbox(tmp_path):
    builders = default_builders(make_settings(tmp_path))
    provider = builders.channels["telegram"]
    assert provider.kind is Channel.TELEGRAM
    assert provider.build_parser is not None and provider.build_inbox is None
    assert provider.answer_unknown is not None
    assert builders.channels["whatsapp"].answer_unknown is None
    assert builders.channels["email"].answer_unknown is None


def test_the_descriptor_builds_what_the_runtime_needs_from_a_real_config(room):
    r = room
    config = r.application.registry.get_config("alpha")
    channel = telegram_provider.build_channel(config)
    assert channel.channel is Channel.TELEGRAM
    assert r.tokens["alpha"] not in repr(channel)
    assert telegram_provider.routing_key(config) == "1000000"
    assert telegram_provider.webhook_secret(config) == r.secret_of("alpha")
    assert not config.missing_credentials
