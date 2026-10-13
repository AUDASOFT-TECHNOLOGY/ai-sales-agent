"""Telegram webhook authentication and parsing, invitations, and the connection probe (no network).

Payloads follow the shape of the Bot API's `Update`; none of them came from a real bot.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest

from sales_core.contracts import Channel
from sales_core.ports import InvalidSignatureError, MalformedPayloadError, WebhookParser
from sales_integrations.telegram import (
    SECRET_HEADER,
    BotCheck,
    BotCheckError,
    TelegramWebhookParser,
    invitation_link,
    make_invitation,
    read_invitation,
)

SECRETS = {"111": "secret-one-" + "a" * 24, "222": "secret-two-" + "b" * 24}
ONE = SECRETS["111"]
TWO = SECRETS["222"]
CHAT = 424242


def parser(secrets=None) -> TelegramWebhookParser:
    return TelegramWebhookParser(app_secrets=SECRETS if secrets is None else secrets)


def update(message=None, **extra):
    base = {"update_id": 9001}
    if message is not None:
        base["message"] = message
    return base | extra


def msg(**fields):
    base = {
        "message_id": 5,
        "date": 1768467600,
        "chat": {"id": CHAT, "type": "private", "first_name": "Sam"},
        "from": {"id": CHAT, "is_bot": False, "first_name": "Sam", "last_name": "Doe"},
    }
    return base | ({"text": "Hello"} if "text" not in fields and len(fields) == 0 else {}) | fields


def post(body, secret=ONE, name=SECRET_HEADER):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return raw, ({name: secret} if secret is not None else {})


def parse(body, **kw):
    raw, headers = post(body, **kw)
    return parser().parse(raw, headers)


def one(body, **kw):
    (event,) = parse(body, **kw)
    return event


def test_satisfies_the_port_and_has_no_handshake():
    assert isinstance(parser(), WebhookParser)
    assert parser().handshake({"hub.mode": "subscribe", "hub.challenge": "1"}) is None


class TestAuthenticity:
    def test_a_known_secret_routes_to_its_client(self):
        assert one(update(msg(text="Hi")), secret=ONE).routing_key == "111"
        assert one(update(msg(text="Hi")), secret=TWO).routing_key == "222"

    @pytest.mark.parametrize("secret", [None, "", "wrong", ONE + "x", ONE[:-1], ONE.upper()])
    def test_anything_else_is_refused_whole(self, secret):
        raw, headers = post(update(msg(text="Hi")), secret=secret)
        with pytest.raises(InvalidSignatureError):
            parser().parse(raw, headers)

    def test_the_secret_is_checked_before_the_body_is_read(self):
        with pytest.raises(InvalidSignatureError):
            parser().parse(b"not json at all", {SECRET_HEADER: "wrong"})

    def test_the_header_name_is_case_insensitive(self):
        raw, headers = post(update(msg(text="Hi")), name="X-Telegram-Bot-Api-Secret-Token")
        assert len(parser().parse(raw, headers)) == 1

    def test_with_no_client_every_request_is_refused(self):
        raw, headers = post(update(msg(text="Hi")))
        with pytest.raises(InvalidSignatureError):
            parser({}).parse(raw, headers)

    def test_a_client_without_a_secret_is_never_served_by_an_empty_one(self):
        raw, headers = post(update(msg(text="Hi")), secret="")
        with pytest.raises(InvalidSignatureError):
            parser({"111": ""}).parse(raw, headers)

    def test_one_clients_secret_never_reaches_another_client(self):
        assert one(update(msg(text="Hi")), secret=TWO).routing_key != "111"


class TestPayload:
    def test_a_text_message(self):
        event = one(update(msg(text="Hello there")))
        message = event.message
        assert message.channel is Channel.TELEGRAM and message.sender == str(CHAT)
        assert message.text == "Hello there" and message.external_id == f"{CHAT}:5"
        assert message.timestamp == datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
        assert message.metadata["type"] == "text" and message.metadata["sender_name"] == "Sam Doe"
        assert message.linked_prospect_id is None and message.shared_phone is None

    @pytest.mark.parametrize("not_json", [b"", b"nope", b"\xff\xfe", b"[]", b"1", b'"x"'])
    def test_what_is_not_an_update_is_malformed(self, not_json):
        with pytest.raises(MalformedPayloadError):
            parse(not_json)

    def test_an_update_with_no_id_is_malformed(self):
        with pytest.raises(MalformedPayloadError):
            parse({"message": msg(text="x")})

    @pytest.mark.parametrize("broken", [{"text": "x"}, {"chat": {"id": "abc", "type": "private"}}])
    def test_a_message_missing_what_it_needs_is_malformed(self, broken):
        base = msg(text="x")
        if "chat" in broken:
            base = base | broken
        else:
            base = {k: v for k, v in base.items() if k != "date"}
        with pytest.raises(MalformedPayloadError):
            parse(update(base))

    @pytest.mark.parametrize(
        "other",
        [
            update(edited_message=msg(text="edited")),
            update(callback_query={"id": "1"}),
            update(my_chat_member={"chat": {"id": 1}}),
            update(channel_post=msg(text="post")),
            update(),
        ],
    )
    def test_what_is_not_a_new_message_is_acknowledged_and_ignored(self, other):
        assert parse(other) == []

    @pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
    def test_only_private_chats_are_conversations(self, chat_type):
        message = msg(text="hi") | {"chat": {"id": -100123, "type": chat_type}}
        assert parse(update(message)) == []

    def test_another_bot_is_not_a_prospect(self):
        message = msg(text="hi") | {"from": {"id": 7, "is_bot": True, "first_name": "Bot"}}
        assert parse(update(message)) == []

    def test_a_message_with_no_text_keeps_its_kind_and_a_reference_not_a_copy(self):
        photo = msg(photo=[{"file_id": "small", "width": 1}, {"file_id": "large", "width": 9}])
        message = one(update(photo | {"caption": "my receipt"})).message
        assert message.text == "my receipt"
        assert message.metadata == {
            "type": "photo",
            "sender_name": "Sam Doe",
            "media_id": "large",
        }
        voice = one(update(msg(voice={"file_id": "v1", "duration": 3}))).message
        assert voice.text == "" and voice.metadata["type"] == "voice"

    def test_an_unknown_kind_of_message_is_kept_empty_not_dropped(self):
        message = one(update(msg(poll={"id": "1"}))).message
        assert message.text == "" and message.metadata["type"] == "unknown"

    def test_the_sender_name_is_bounded_and_may_be_absent(self):
        long_name = msg(text="x") | {"from": {"id": CHAT, "first_name": "N" * 500}}
        assert len(one(update(long_name)).message.metadata["sender_name"]) <= 80
        anonymous = msg(text="x") | {"from": {"id": CHAT}}
        assert "sender_name" not in one(update(anonymous)).message.metadata


class TestStartAndContact:
    def test_a_plain_start_carries_no_text_and_no_claim(self):
        message = one(update(msg(text="/start"))).message
        assert message.text == "" and message.metadata["type"] == "start"
        assert message.linked_prospect_id is None

    def test_a_start_with_one_of_our_invitations_names_the_prospect(self):
        payload = make_invitation(ONE, "p-0042")
        message = one(update(msg(text=f"/start {payload}"))).message
        assert message.linked_prospect_id == "p-0042" and message.text == ""

    def test_the_command_may_name_the_bot(self):
        payload = make_invitation(ONE, "p1")
        message = one(update(msg(text=f"/start@sales_bot {payload}"))).message
        assert message.linked_prospect_id == "p1"

    def test_an_invitation_made_for_another_client_names_nobody(self):
        payload = make_invitation(TWO, "p1")
        message = one(update(msg(text=f"/start {payload}")), secret=ONE).message
        assert message.linked_prospect_id is None

    @pytest.mark.parametrize("payload", ["garbage", "a" * 30, "x" * 80, "../etc", "%00"])
    def test_a_payload_that_is_not_an_invitation_is_no_claim(self, payload):
        assert one(update(msg(text=f"/start {payload}"))).message.linked_prospect_id is None

    def test_a_person_typing_start_words_in_a_sentence_is_not_starting(self):
        payload = make_invitation(ONE, "p1")
        message = one(update(msg(text=f"hello /start {payload}"))).message
        assert message.linked_prospect_id is None and message.metadata["type"] == "text"

    def test_their_own_shared_number_is_a_verified_claim_and_is_not_kept_in_the_text(self):
        contact = {"phone_number": "+21620000001", "first_name": "Sam", "user_id": CHAT}
        message = one(update(msg(contact=contact))).message
        assert message.shared_phone == "+21620000001" and message.text == ""
        assert "21620000001" not in str(dict(message.metadata))

    def test_someone_elses_number_is_not_a_claim(self):
        contact = {"phone_number": "+21620000009", "first_name": "Other", "user_id": 777}
        assert one(update(msg(contact=contact))).message.shared_phone is None
        unlinked = {"phone_number": "+21620000009", "first_name": "Other"}
        assert one(update(msg(contact=unlinked))).message.shared_phone is None

    def test_a_number_typed_as_text_is_not_a_claim(self):
        assert one(update(msg(text="my number is +21620000001"))).message.shared_phone is None


class TestInvitations:
    def test_an_invitation_round_trips(self):
        payload = make_invitation(ONE, "p-0042")
        assert payload and read_invitation(ONE, payload) == "p-0042"

    def test_it_fits_telegrams_limit_and_alphabet(self):
        payload = make_invitation(ONE, "hubspot:123456789012")
        assert payload and len(payload) <= 64 and all(c.isalnum() or c in "-_" for c in payload)

    def test_a_non_ascii_id_round_trips(self):
        payload = make_invitation(ONE, "prospect-é")
        assert payload and read_invitation(ONE, payload) == "prospect-é"

    def test_an_id_too_long_for_a_link_has_none(self):
        assert make_invitation(ONE, "p" * 80) is None
        assert make_invitation(ONE, "") is None

    def test_no_other_secret_reads_it(self):
        assert read_invitation(TWO, make_invitation(ONE, "p1")) is None

    def test_one_prospect_cannot_be_turned_into_another(self):
        payload = make_invitation(ONE, "p1")
        other = make_invitation(ONE, "p2")
        assert read_invitation(ONE, payload[:16] + other[16:]) is None
        assert read_invitation(ONE, payload[:-1] + ("A" if payload[-1] != "A" else "B")) is None

    def test_the_link_names_the_bot(self):
        assert invitation_link("sales_bot", "abc") == "https://t.me/sales_bot?start=abc"


class TestTheProbe:
    URL = "https://platform.example/webhooks/telegram"

    def check(self, handler):
        return BotCheck(
            "123456789:token", client=httpx.Client(transport=httpx.MockTransport(handler))
        )

    def test_it_names_the_bot(self):
        check = self.check(
            lambda r: httpx.Response(200, json={"ok": True, "result": {"username": "sales_bot"}})
        )
        assert check.username() == "sales_bot"

    @pytest.mark.parametrize(
        ("status", "kind"),
        [(401, "auth"), (404, "auth"), (429, "rate_limited"), (500, "service_error")],
    )
    def test_a_refusal_is_a_kind_and_nothing_else(self, status, kind):
        body = {"ok": False, "error_code": status, "description": "token 123456789:token leaked"}
        check = self.check(lambda r: httpx.Response(status, json=body))
        with pytest.raises(BotCheckError) as error:
            check.username()
        assert error.value.kind == kind and "token" not in str(error.value)

    def test_a_network_failure_is_unreachable(self):
        def down(request):
            raise httpx.ConnectError("down")

        with pytest.raises(BotCheckError) as error:
            self.check(down).username()
        assert error.value.kind == "unreachable"

    def test_registering_sends_the_url_the_secret_and_only_messages(self):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": True})

        self.check(handler).register_webhook(self.URL, ONE)
        assert seen == [
            {
                "url": self.URL,
                "secret_token": ONE,
                "allowed_updates": ["message"],
                "drop_pending_updates": False,
            }
        ]

    def test_an_address_telegram_refuses_is_told_as_such(self):
        body = {"ok": False, "error_code": 400, "description": "Bad Request: bad webhook"}
        with pytest.raises(BotCheckError) as error:
            self.check(lambda r: httpx.Response(400, json=body)).register_webhook(self.URL, ONE)
        assert error.value.kind == "rejected"

    @pytest.mark.parametrize(
        ("info", "state"),
        [
            ({"url": ""}, "not_set"),
            ({"url": "https://elsewhere.example/hook"}, "other_url"),
            ({"url": URL}, "ok"),
            ({"url": URL, "last_error_date": 1, "pending_update_count": 3}, "failing"),
            ({"url": URL, "last_error_date": 1, "pending_update_count": 0}, "ok"),
        ],
    )
    def test_where_telegram_delivers(self, info, state):
        check = self.check(lambda r: httpx.Response(200, json={"ok": True, "result": info}))
        assert check.webhook_state(self.URL) == state
