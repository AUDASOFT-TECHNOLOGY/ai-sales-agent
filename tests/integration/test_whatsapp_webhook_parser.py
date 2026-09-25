"""WhatsApp webhook authentication and parsing (no network)."""

import json
from datetime import UTC, datetime

import pytest

from sales_core.contracts import Channel
from sales_core.ports import InvalidSignatureError, MalformedPayloadError, WebhookParser
from sales_integrations.whatsapp import WhatsAppWebhookParser, compute_signature

SECRETS = {"111": "secret-one", "222": "secret-two"}
TOKEN = "verify-token"


def parser() -> WhatsAppWebhookParser:
    return WhatsAppWebhookParser(app_secrets=SECRETS, verify_token=TOKEN)


def message(**fields):
    base = {"from": "21612345678", "id": "wamid.1", "timestamp": "1768467600", "type": "text"}
    return base | ({"text": {"body": "Hello"}} if "type" not in fields else {}) | fields


def payload(phone="111", messages=None, **value_extra):
    value = {"metadata": {"phone_number_id": phone}} | value_extra
    if messages is not None:
        value["messages"] = messages
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": value}]}],
    }


def signed(body: dict, secret="secret-one") -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    return raw, {"X-Hub-Signature-256": compute_signature(secret, raw)}


def test_satisfies_the_port():
    assert isinstance(parser(), WebhookParser)


class TestHandshake:
    def test_echoes_the_challenge(self):
        query = {"hub.mode": "subscribe", "hub.verify_token": TOKEN, "hub.challenge": "777"}
        assert parser().handshake(query) == "777"

    @pytest.mark.parametrize(
        "query",
        [
            {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
            {"hub.mode": "other", "hub.verify_token": TOKEN, "hub.challenge": "1"},
            {"hub.mode": "subscribe", "hub.challenge": "1"},
            {},
        ],
    )
    def test_refuses_everything_else(self, query):
        assert parser().handshake(query) is None

    def test_without_a_configured_token_nothing_is_accepted(self):
        unconfigured = WhatsAppWebhookParser(app_secrets=SECRETS, verify_token=None)
        query = {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}
        assert unconfigured.handshake(query) is None


class TestSignature:
    def test_a_valid_signature_is_accepted(self):
        raw, headers = signed(payload(messages=[message()]))
        assert len(parser().parse(raw, headers)) == 1

    def test_header_name_is_case_insensitive(self):
        raw, headers = signed(payload(messages=[message()]))
        lowered = {k.lower(): v for k, v in headers.items()}
        assert len(parser().parse(raw, lowered)) == 1

    def test_the_signature_is_over_the_exact_bytes(self):
        raw, headers = signed(payload(messages=[message()]))
        with pytest.raises(InvalidSignatureError):
            parser().parse(raw + b" ", headers)

    @pytest.mark.parametrize(
        "headers", [{}, {"X-Hub-Signature-256": ""}, {"X-Hub-Signature-256": "sha256=deadbeef"}]
    )
    def test_missing_or_wrong_signatures_are_refused(self, headers):
        raw = json.dumps(payload(messages=[message()])).encode()
        with pytest.raises(InvalidSignatureError):
            parser().parse(raw, headers)

    def test_each_number_is_verified_with_its_own_secret(self):
        raw, headers = signed(payload(phone="222", messages=[message()]), secret="secret-one")
        with pytest.raises(InvalidSignatureError):
            parser().parse(raw, headers)
        raw, headers = signed(payload(phone="222", messages=[message()]), secret="secret-two")
        assert parser().parse(raw, headers)[0].routing_key == "222"

    def test_a_number_with_no_configured_secret_is_returned_for_the_caller_to_log(self):
        raw = json.dumps(payload(phone="999", messages=[message()])).encode()
        (event,) = parser().parse(raw, {})
        assert event.routing_key == "999"


class TestParsing:
    def test_text_message(self):
        raw, headers = signed(payload(messages=[message(id="wamid.42", **{"from": "216 111"})]))
        (event,) = parser().parse(raw, headers)
        assert event.routing_key == "111"
        m = event.message
        assert m.channel is Channel.WHATSAPP and m.sender == "216 111"
        assert m.external_id == "wamid.42" and m.text == "Hello"
        assert m.timestamp == datetime.fromtimestamp(1768467600, UTC)
        assert m.metadata == {"type": "text"}

    def test_button_and_interactive_replies_use_their_visible_text(self):
        messages = [
            message(id="a", type="button", button={"text": "Yes please", "payload": "p"}),
            message(id="b", type="interactive", interactive={"button_reply": {"title": "Later"}}),
            message(id="c", type="interactive", interactive={"list_reply": {"title": "Option 2"}}),
        ]
        raw, headers = signed(payload(messages=messages))
        texts = [e.message.text for e in parser().parse(raw, headers)]
        assert texts == ["Yes please", "Later", "Option 2"]

    def test_media_has_no_text_but_keeps_its_kind(self):
        raw, headers = signed(payload(messages=[message(type="image", image={"id": "x"})]))
        (event,) = parser().parse(raw, headers)
        assert event.message.text == ""
        # the kind, and the provider's reference to the media: nothing is downloaded or stored
        assert event.message.metadata == {"type": "image", "media_id": "x"}

    def test_media_without_an_id_keeps_only_its_kind(self):
        raw, headers = signed(payload(messages=[message(type="document", document={})]))
        (event,) = parser().parse(raw, headers)
        assert event.message.metadata == {"type": "document"}

    def test_several_messages_in_one_delivery(self):
        raw, headers = signed(payload(messages=[message(id="1"), message(id="2")]))
        assert [e.message.external_id for e in parser().parse(raw, headers)] == ["1", "2"]

    def test_status_updates_are_not_messages(self):
        raw, headers = signed(payload(statuses=[{"id": "wamid.9", "status": "read"}]))
        assert parser().parse(raw, headers) == []

    @pytest.mark.parametrize(
        "body",
        [
            b"not json",
            b"\xff\xfe",
            b"[]",
            b'{"object": "instagram"}',
            b'{"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {}}]}]}',
            b'{"object": "whatsapp_business_account", "entry": "nope"}',
        ],
    )
    def test_malformed_bodies(self, body):
        with pytest.raises(MalformedPayloadError):
            parser().parse(body, {})

    @pytest.mark.parametrize(
        "broken",
        [
            {"id": "1", "timestamp": "1", "type": "text", "text": {"body": "x"}},
            message(timestamp="soon"),
        ],
    )
    def test_unreadable_messages_are_malformed(self, broken):
        raw, headers = signed(payload(messages=[broken]))
        with pytest.raises(MalformedPayloadError):
            parser().parse(raw, headers)

    def test_malformed_is_reported_before_authenticity_so_garbage_is_a_400(self):
        with pytest.raises(MalformedPayloadError):
            parser().parse(b"garbage", {"X-Hub-Signature-256": "sha256=wrong"})
