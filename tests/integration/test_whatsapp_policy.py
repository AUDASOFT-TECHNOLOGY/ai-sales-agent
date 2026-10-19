"""WhatsApp's rules in the channel and the webhook: the 24-hour window, templates, error codes,
delivery statuses and template events. Against a mocked HTTP transport (no network)."""

import dataclasses
import json
from datetime import timedelta

import httpx
import pytest
from factories import NOW

from sales_core.contracts import Channel, MessageDraft, OutboundMessage, SendStatus
from sales_core.contracts.messaging import DEFERRED_KEY, DEFERRED_VALUE
from sales_core.ports import InvalidSignatureError, MalformedPayloadError
from sales_integrations.fakes import FakeClock
from sales_integrations.whatsapp import (
    DELIVERY,
    TEMPLATE,
    OpeningTemplate,
    SendPolicy,
    WhatsAppChannel,
    WhatsAppWebhookParser,
    compute_signature,
)

OPENING = OpeningTemplate("hello_v1", "fr", ("1",))


def channel(handler, policy):
    seen = []

    def record(request):
        seen.append(request)
        return handler(request)

    return (
        WhatsAppChannel(
            token="tok",
            phone_number_id="PHONEID",
            client=httpx.Client(transport=httpx.MockTransport(record)),
            clock=FakeClock(NOW),
            policy=policy,
        ),
        seen,
    )


def ok(request):
    return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})


def message(text="Bonjour\n  Alex"):
    return OutboundMessage(
        client_id="a",
        prospect_id="p",
        channel=Channel.WHATSAPP,
        recipient="+216 20 000 001",
        draft=MessageDraft(text=text),
        idempotency_key="k",
    )


def policy(last=None, opening=OPENING, **hooks):
    return SendPolicy(
        last_inbound=lambda digits: last if digits == "21620000001" else None,
        opening_template=lambda: opening,
        **hooks,
    )


class TestTheTwentyFourHourWindow:
    def test_free_text_inside_the_window(self):
        sender, seen = channel(ok, policy(last=NOW - timedelta(hours=23)))
        assert sender.send(message()).status is SendStatus.ACCEPTED
        assert json.loads(seen[0].content)["type"] == "text"

    def test_the_opening_template_carries_the_text_outside_it(self):
        sender, seen = channel(ok, policy(last=NOW - timedelta(hours=25)))
        assert sender.send(message()).status is SendStatus.ACCEPTED
        body = json.loads(seen[0].content)
        assert body["type"] == "template" and body["template"]["name"] == "hello_v1"
        assert body["template"]["language"] == {"code": "fr"}
        # One line, as a template variable requires.
        assert body["template"]["components"][0]["parameters"] == [
            {"type": "text", "text": "Bonjour Alex"}
        ]

    def test_a_person_who_never_wrote_is_outside_it(self):
        sender, seen = channel(ok, policy(last=None))
        sender.send(message())
        assert json.loads(seen[0].content)["type"] == "template"

    @pytest.mark.parametrize(
        "opening",
        [None, OpeningTemplate("no_variable", "fr", ()), OpeningTemplate("two", "fr", ("1", "2"))],
    )
    def test_without_a_usable_template_the_message_waits_and_nothing_is_sent(self, opening):
        sender, seen = channel(ok, policy(last=None, opening=opening))
        result = sender.send(message())
        assert seen == []
        assert (
            result.status is SendStatus.FAILED_TRANSIENT
            and result.error_code == "template_required"
        )
        assert result.metadata[DEFERRED_KEY] == DEFERRED_VALUE  # not counted as an attempt

    def test_an_explicit_template_is_never_replaced(self):
        sender, seen = channel(ok, policy(last=None))
        draft = MessageDraft(text="", template_id="other", language="en")
        sender.send(dataclasses.replace(message(), draft=draft))
        assert json.loads(seen[0].content)["template"]["name"] == "other"

    def test_named_variables_are_sent_by_name(self):
        sender, seen = channel(ok, policy(last=None, opening=OpeningTemplate("n", "fr", ("name",))))
        sender.send(message("Salut"))
        parameter = json.loads(seen[0].content)["template"]["components"][0]["parameters"][0]
        assert parameter == {"type": "text", "parameter_name": "name", "text": "Salut"}


def reject(code, status=400):
    return lambda request: httpx.Response(status, json={"error": {"code": code, "message": "x"}})


class TestMetaErrors:
    @pytest.mark.parametrize(
        "code", [131047, 131049, 131056, 130429, 131048, 131042, 133010, 131031]
    )
    def test_account_or_moment_problems_wait_without_using_an_attempt(self, code):
        sender, _ = channel(reject(code), policy(last=NOW))
        result = sender.send(message())
        assert result.status is SendStatus.FAILED_TRANSIENT
        assert result.metadata[DEFERRED_KEY] == DEFERRED_VALUE

    @pytest.mark.parametrize("code", [131026, 131030, 131021])
    def test_a_problem_of_this_recipient_is_permanent(self, code):
        sender, _ = channel(reject(code), policy(last=NOW))
        assert sender.send(message()).status is SendStatus.FAILED_PERMANENT

    def test_a_refused_token_asks_for_a_reconnection(self):
        calls = []
        sender, _ = channel(
            reject(190, 401), policy(last=NOW, on_auth_failure=lambda: calls.append(1))
        )
        result = sender.send(message())
        assert calls == [1] and result.status is SendStatus.FAILED_TRANSIENT

    def test_a_paused_template_is_reported_with_its_name(self):
        seen = []
        sender, _ = channel(
            reject(132015), policy(last=None, on_template_problem=lambda *a: seen.append(a))
        )
        result = sender.send(message())
        assert seen == [("hello_v1", "fr", "132015")]
        assert result.metadata[DEFERRED_KEY] == DEFERRED_VALUE

    def test_a_window_meta_says_is_over_is_forgotten_so_the_next_try_uses_a_template(self):
        closed = []
        sender, _ = channel(reject(131047), policy(last=NOW, on_window_closed=closed.append))
        sender.send(message())
        assert closed == ["21620000001"]

    def test_a_channel_without_a_policy_sends_what_it_is_given(self):
        sender, seen = channel(ok, None)
        sender.send(message())
        assert json.loads(seen[0].content)["type"] == "text"


SECRET = "app-secret"


def parser():
    return WhatsAppWebhookParser(app_secrets={"PHONE": SECRET}, verify_token="v")


def signed(payload):
    body = json.dumps(payload).encode()
    return body, {"X-Hub-Signature-256": compute_signature(SECRET, body)}


def status_payload(status="delivered", errors=None):
    entry = {
        "id": "WABA",
        "changes": [
            {
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "PHONE"},
                    "statuses": [
                        {
                            "id": "wamid.1",
                            "status": status,
                            "timestamp": "1790000000",
                            "recipient_id": "21620000001",
                            **({"errors": errors} if errors else {}),
                        }
                    ],
                },
            }
        ],
    }
    return {"object": "whatsapp_business_account", "entry": [entry]}


def template_payload():
    value = {
        "event": "APPROVED",
        "message_template_id": 123,
        "message_template_name": "hello_v1",
        "message_template_language": "fr",
    }
    change = {"field": "message_template_status_update", "value": value}
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [change]}]}


class TestNotifications:
    def test_a_delivery_status_is_read_with_its_error(self):
        body, headers = signed(
            status_payload("failed", [{"code": 131026, "title": "Message undeliverable"}])
        )
        (note,) = parser().notifications(body, headers)
        assert note.kind == DELIVERY and note.waba_id == "WABA" and note.phone_number_id == "PHONE"
        assert note.data["status"] == "failed" and note.data["error_code"] == "131026"
        assert note.data["message_id"] == "wamid.1"

    def test_a_template_event_is_read_and_does_not_break_message_parsing(self):
        body, headers = signed(template_payload())
        assert parser().parse(body, headers) == []  # no phone number: not a message, not malformed
        (note,) = parser().notifications(body, headers)
        assert note.kind == TEMPLATE and note.data["name"] == "hello_v1"
        assert note.data["event"] == "APPROVED" and note.data["template_id"] == "123"

    def test_a_status_without_a_signature_is_refused(self):
        body = json.dumps(status_payload()).encode()
        with pytest.raises(InvalidSignatureError):
            parser().notifications(body, {})
        with pytest.raises(InvalidSignatureError):
            parser().notifications(body, {"X-Hub-Signature-256": compute_signature("other", body)})

    def test_a_payload_of_messages_only_needs_no_notification_signature(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {"field": "messages", "value": {"metadata": {"phone_number_id": "P"}}}
                    ]
                }
            ],
        }
        assert parser().notifications(json.dumps(payload).encode(), {}) == []

    def test_a_status_with_no_timestamp_is_malformed(self):
        payload = status_payload()
        del payload["entry"][0]["changes"][0]["value"]["statuses"][0]["timestamp"]
        body, headers = signed(payload)
        with pytest.raises(MalformedPayloadError):
            parser().notifications(body, headers)

    def test_a_reaction_is_not_a_message_and_an_image_caption_is_its_text(self):
        value = {
            "metadata": {"phone_number_id": "PHONE"},
            "contacts": [{"wa_id": "21620000001", "profile": {"name": "Alex"}}],
            "messages": [
                {
                    "from": "21620000001",
                    "id": "r",
                    "timestamp": "1790000000",
                    "type": "reaction",
                    "reaction": {"emoji": "x"},
                },
                {
                    "from": "21620000001",
                    "id": "i",
                    "timestamp": "1790000000",
                    "type": "image",
                    "image": {"id": "media1", "caption": "my receipt"},
                },
            ],
        }
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"id": "W", "changes": [{"field": "messages", "value": value}]}],
        }
        body, headers = signed(payload)
        (event,) = parser().parse(body, headers)
        assert event.message.text == "my receipt"
        assert event.message.metadata["media_id"] == "media1"
        assert event.message.metadata["profile_name"] == "Alex"
