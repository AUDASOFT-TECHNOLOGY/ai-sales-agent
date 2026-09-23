"""Sending through WhatsApp, against a mocked HTTP transport (no network)."""

import json
from datetime import timedelta

import httpx
import pytest
from engine_harness import Crash, ScriptedBrain, build, due
from factories import NOW, make_client_context

from sales_core.approval import ApprovalMode
from sales_core.contracts import (
    ActionStatus,
    Channel,
    MessageDraft,
    OutboundMessage,
    SendStatus,
)
from sales_core.engine import Engine, EngineConfig
from sales_core.ports import MessagingChannel
from sales_integrations.fakes import FakeClock
from sales_integrations.whatsapp import WhatsAppChannel

URL = "https://graph.facebook.com/v21.0/PHONEID/messages"


def channel_with(handler) -> tuple[WhatsAppChannel, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return WhatsAppChannel(
        token="tok", phone_number_id="PHONEID", client=client, clock=FakeClock(NOW)
    ), seen


def outbound(draft=None, recipient="+216 12 345 678") -> OutboundMessage:
    return OutboundMessage(
        client_id="client-a",
        prospect_id="p1",
        channel=Channel.WHATSAPP,
        recipient=recipient,
        draft=draft or MessageDraft(text="Hello there"),
        idempotency_key="k1",
    )


def ok(request):
    return httpx.Response(200, json={"messages": [{"id": "wamid.SENT"}]})


def error(status, code=None, message="boom"):
    body = {"error": {"code": code, "message": message}} if code is not None else {"oops": True}
    return lambda request: httpx.Response(status, json=body)


def test_satisfies_the_port():
    channel, _ = channel_with(ok)
    assert isinstance(channel, MessagingChannel) and channel.channel is Channel.WHATSAPP


class TestRequest:
    def test_text_message(self):
        channel, seen = channel_with(ok)
        result = channel.send(outbound())
        (request,) = seen
        assert str(request.url) == URL and request.headers["Authorization"] == "Bearer tok"
        assert json.loads(request.content) == {
            "messaging_product": "whatsapp",
            "to": "21612345678",  # digits only
            "type": "text",
            "text": {"body": "Hello there"},
        }
        assert result.status is SendStatus.ACCEPTED and result.external_id == "wamid.SENT"
        assert result.timestamp == NOW

    def test_template_message(self):
        channel, seen = channel_with(ok)
        draft = MessageDraft(
            text="",
            template_id="welcome_v1",
            language="fr",
            template_params={"1": "Alex", "2": "Tunis"},
        )
        channel.send(outbound(draft))
        assert json.loads(seen[0].content) == {
            "messaging_product": "whatsapp",
            "to": "21612345678",
            "type": "template",
            "template": {
                "name": "welcome_v1",
                "language": {"code": "fr"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": "Alex"},
                            {"type": "text", "text": "Tunis"},
                        ],
                    }
                ],
            },
        }

    def test_a_template_without_parameters_has_no_components(self):
        channel, seen = channel_with(ok)
        channel.send(outbound(MessageDraft(text="", template_id="t", language="en")))
        assert "components" not in json.loads(seen[0].content)["template"]

    @pytest.mark.parametrize(
        ("message", "reason"),
        [
            (outbound(recipient="no digits"), "no digits"),
            (outbound(MessageDraft(text="", template_id="t")), "language"),
        ],
    )
    def test_unsendable_messages_fail_permanently_without_calling_the_api(self, message, reason):
        channel, seen = channel_with(ok)
        result = channel.send(message)
        assert result.status is SendStatus.FAILED_PERMANENT and reason in result.error_message
        assert seen == []


class TestOutcomes:
    def test_rejection_for_this_recipient_is_permanent_and_carries_meta_code(self):
        channel, _ = channel_with(error(400, 131026, "Message undeliverable"))
        result = channel.send(outbound())
        assert result.status is SendStatus.FAILED_PERMANENT
        assert result.error_code == "131026" and "undeliverable" in result.error_message

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    def test_rate_limits_and_server_errors_are_transient(self, status):
        channel, _ = channel_with(error(status, 4, "slow down"))
        assert channel.send(outbound()).status is SendStatus.FAILED_TRANSIENT

    @pytest.mark.parametrize(("status", "code"), [(401, 190), (403, 10), (400, 190), (400, 200)])
    def test_broken_credentials_are_our_problem_not_the_prospects(self, status, code):
        """Reported as retryable so the Engine does not pause the prospect for an expired token."""
        channel, _ = channel_with(error(status, code, "token expired"))
        assert channel.send(outbound()).status is SendStatus.FAILED_TRANSIENT

    def test_a_non_json_error_body_still_yields_a_readable_failure(self):
        channel, _ = channel_with(lambda request: httpx.Response(404, text="<html>gone</html>"))
        result = channel.send(outbound())
        assert result.status is SendStatus.FAILED_PERMANENT and "404" in result.error_message

    def test_a_refused_connection_means_nothing_was_sent_so_it_is_transient(self):
        def refuse(request):
            raise httpx.ConnectError("connection refused")

        channel, _ = channel_with(refuse)
        assert channel.send(outbound()).status is SendStatus.FAILED_TRANSIENT

    @pytest.mark.parametrize(
        "exception", [httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("reset")]
    )
    def test_a_failure_after_the_request_left_is_unknown_and_is_raised(self, exception):
        def fail(request):
            raise exception

        channel, _ = channel_with(fail)
        with pytest.raises(type(exception)):
            channel.send(outbound())

    def test_a_success_reply_without_an_id_is_unknown_and_is_raised(self):
        channel, _ = channel_with(lambda request: httpx.Response(200, json={"messages": []}))
        with pytest.raises(RuntimeError, match="unreadable"):
            channel.send(outbound())


class TestWithTheEngine:
    """The adapter's outcomes must drive the Engine's duplicate-send protection correctly."""

    def _harness(self, handler):
        h = build(prospects=[due("p1")], brain=ScriptedBrain())
        real, seen = channel_with(handler)
        engine = Engine(
            client=make_client_context(),
            repository=h.repo,
            brain=h.brain,
            channel=real,
            clock=h.clock,
            config=EngineConfig(mode=ApprovalMode.APPROVAL),
        )
        engine.prepare()
        h.approve("p1")
        return h, engine, seen

    def test_an_unknown_outcome_leaves_the_action_in_sending_and_it_is_never_resent(self):
        calls = []

        def timeout(request):
            calls.append(1)
            raise httpx.ReadTimeout("no answer")

        h, engine, _ = self._harness(timeout)
        report = engine.dispatch()
        assert h.status("p1") is ActionStatus.SENDING and not report.ok
        engine.dispatch()
        engine.dispatch()
        assert len(calls) == 1  # one attempt only, however often dispatch runs

    def test_an_expired_token_does_not_pause_the_prospect(self):
        h, engine, _ = self._harness(error(401, 190, "expired"))
        before = h.prospect("p1")
        engine.dispatch()
        assert h.prospect("p1") == before  # still ACTIVE, schedule untouched
        assert h.status("p1") is ActionStatus.APPROVED  # will be retried once the token is fixed

    def test_an_accepted_message_completes_the_flow(self):
        h, engine, seen = self._harness(ok)
        engine.dispatch()
        assert h.status("p1") is ActionStatus.SENT and len(seen) == 1
        assert h.repo.messages[-1].external_id == "wamid.SENT"

    def test_a_dying_process_mid_request_is_covered_by_the_sending_guard(self):
        def crash(request):
            raise Crash

        h, engine, _ = self._harness(crash)
        with pytest.raises(Crash):
            engine.dispatch()
        assert h.status("p1") is ActionStatus.SENDING
        h.clock.advance(timedelta(minutes=5))
        assert not engine.dispatch().ok  # flagged for a human, not resent
