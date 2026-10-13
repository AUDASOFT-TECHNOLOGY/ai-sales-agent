"""Sending through a client's Telegram bot, against a mocked HTTP transport (no network).

Nothing here talks to Telegram: it proves what the adapter sends and how it reads the answers
Telegram is documented to give, not that a real bot accepts them.
"""

import json

import httpx
import pytest
from factories import NOW

from sales_core.contracts import Channel, MessageDraft, OutboundMessage, SendStatus
from sales_core.ports import MessagingChannel
from sales_integrations.fakes import FakeClock
from sales_integrations.telegram import TelegramApi, TelegramChannel, chunks

TOKEN = "123456789:AAH-secret-token-value-of-the-bot_xx"
URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


def channel_with(handler) -> tuple[TelegramChannel, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return TelegramChannel(token=TOKEN, client=client, clock=FakeClock(NOW)), seen


def outbound(text="Hello there", recipient="424242", draft=None) -> OutboundMessage:
    return OutboundMessage(
        client_id="client-a",
        prospect_id="p1",
        channel=Channel.TELEGRAM,
        recipient=recipient,
        draft=draft or MessageDraft(text=text),
        idempotency_key="k1",
    )


def ok(request):
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 77, "chat": {}}})


def error(status, description="boom", **extra):
    body = {"ok": False, "error_code": status, "description": description} | extra
    return lambda request: httpx.Response(status, json=body)


def test_satisfies_the_port():
    channel, _ = channel_with(ok)
    assert isinstance(channel, MessagingChannel) and channel.channel is Channel.TELEGRAM


class TestRequest:
    def test_a_text_message_goes_to_the_chat_of_the_prospect(self):
        channel, seen = channel_with(ok)
        result = channel.send(outbound())
        (request,) = seen
        assert str(request.url) == URL
        assert json.loads(request.content) == {"chat_id": "424242", "text": "Hello there"}
        assert result.status is SendStatus.ACCEPTED and result.external_id == "424242:77"
        assert result.timestamp == NOW

    def test_the_text_is_sent_as_it_is_with_no_markup_to_interpret(self):
        channel, seen = channel_with(ok)
        channel.send(outbound("*bold* <b>x</b> _y_ [a](b)"))
        assert "parse_mode" not in json.loads(seen[0].content)

    @pytest.mark.parametrize("recipient", ["+216 12 345 678", "alex@example.com", "@alex", "-100"])
    def test_a_recipient_that_is_not_a_chat_id_is_a_permanent_failure_and_nothing_is_sent(
        self, recipient
    ):
        channel, seen = channel_with(ok)
        result = channel.send(outbound(recipient=recipient))
        assert result.status is SendStatus.FAILED_PERMANENT and seen == []

    def test_a_whatsapp_template_has_no_text_to_send_and_is_not_sent(self):
        channel, seen = channel_with(ok)
        draft = MessageDraft(text="", template_id="welcome", language="fr")
        result = channel.send(outbound(draft=draft))
        assert result.status is SendStatus.FAILED_PERMANENT and seen == []


class TestLongMessages:
    def test_a_text_within_the_limit_is_one_request(self):
        channel, seen = channel_with(ok)
        channel.send(outbound("x" * 4096))
        assert len(seen) == 1

    def test_a_longer_text_is_sent_in_order_in_pieces_and_nothing_is_lost(self):
        text = " ".join(f"word{i}" for i in range(2000))
        channel, seen = channel_with(ok)
        result = channel.send(outbound(text))
        pieces = [json.loads(r.content)["text"] for r in seen]
        assert len(pieces) > 1 and all(len(p) <= 4096 for p in pieces)
        assert " ".join(pieces).split() == text.split()
        assert result.ok

    def test_a_piece_failing_after_another_arrived_is_not_reported_as_a_failure(self):
        calls = []

        def second_fails(request):
            calls.append(request)
            return ok(request) if len(calls) == 1 else error(500)(request)

        channel, _ = channel_with(second_fails)
        with pytest.raises(RuntimeError, match="in part"):
            channel.send(outbound("word " * 3000))

    def test_chunks_are_cut_at_a_boundary_and_never_empty(self):
        assert chunks("short") == ["short"]
        assert chunks("") == [""]
        assert all(chunks("a" * 10000, 4096))
        assert "".join(chunks("a" * 10000, 4096)) == "a" * 10000


class TestAnswers:
    @pytest.mark.parametrize(
        ("status", "description", "expected"),
        [
            (403, "Forbidden: bot was blocked by the user", SendStatus.FAILED_PERMANENT),
            (403, "Forbidden: user is deactivated", SendStatus.FAILED_PERMANENT),
            (400, "Bad Request: chat not found", SendStatus.FAILED_PERMANENT),
            (400, "Bad Request: message is too long", SendStatus.FAILED_PERMANENT),
            (429, "Too Many Requests: retry after 5", SendStatus.FAILED_TRANSIENT),
            (500, "Internal error", SendStatus.FAILED_TRANSIENT),
            (502, "Bad gateway", SendStatus.FAILED_TRANSIENT),
            # our own bot is broken, not this person: the Engine must not pause them for it
            (401, "Unauthorized", SendStatus.FAILED_TRANSIENT),
            (404, "Not Found", SendStatus.FAILED_TRANSIENT),
        ],
    )
    def test_each_refusal_is_told_apart(self, status, description, expected):
        channel, _ = channel_with(error(status, description))
        result = channel.send(outbound())
        assert result.status is expected and description in result.error_message
        assert result.error_code == str(status)

    def test_a_rate_limit_says_how_long_to_wait(self):
        channel, _ = channel_with(error(429, "Too Many Requests", parameters={"retry_after": 12}))
        assert "retry after 12s" in channel.send(outbound()).error_message

    def test_a_refusal_with_no_json_is_still_a_result(self):
        channel, _ = channel_with(lambda r: httpx.Response(502, text="<html>bad gateway</html>"))
        result = channel.send(outbound())
        assert result.status is SendStatus.FAILED_TRANSIENT and "502" in result.error_message

    def test_an_unreadable_success_is_never_guessed_at(self):
        channel, _ = channel_with(lambda r: httpx.Response(200, json={"ok": True, "result": {}}))
        with pytest.raises(RuntimeError, match="unreadable"):
            channel.send(outbound())

    def test_a_connection_that_could_not_be_made_is_transient(self):
        def refused(request):
            raise httpx.ConnectError("refused")

        channel, _ = channel_with(refused)
        result = channel.send(outbound())
        assert result.status is SendStatus.FAILED_TRANSIENT and result.error_code == "connect_error"

    def test_an_outcome_that_is_unknown_raises_so_the_engine_keeps_the_action_for_a_human(self):
        def timed_out(request):
            raise httpx.ReadTimeout("slow")

        channel, _ = channel_with(timed_out)
        with pytest.raises(httpx.ReadTimeout):
            channel.send(outbound())


class TestTheTokenNeverLeaks:
    def test_it_is_in_no_result_and_no_repr(self):
        channel, _ = channel_with(error(401, f"Unauthorized {TOKEN}"))
        result = channel.send(outbound())
        assert TOKEN not in (result.error_message or "") and TOKEN not in repr(result)
        assert TOKEN not in repr(channel) and TOKEN not in repr(TelegramApi(TOKEN))

    def test_a_connection_error_names_its_type_not_its_url(self):
        def refused(request):
            raise httpx.ConnectError(f"cannot connect to {request.url}")

        channel, _ = channel_with(refused)
        assert TOKEN not in channel.send(outbound()).error_message
