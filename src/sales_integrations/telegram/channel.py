"""Sending through a client's own Telegram bot.

A bot can only write to a person who has started it, so the only recipient there is, is a chat id
Telegram itself gave us when that person wrote first (`Prospect.contact.telegram_chat_id`).
"""

from datetime import UTC, datetime

import httpx

from sales_core.contracts import Channel, OutboundMessage, SendResult, SendStatus
from sales_core.ports import Clock
from sales_integrations.telegram.api import BotReply, TelegramApi, chunks

# HTTP statuses that mean *our* bot is broken, not that this person is unreachable: a bad token
# (401, or 404 for a token Telegram does not know). The Engine pauses a prospect on a permanent
# failure, so these are reported as retryable.
_OPERATOR_STATUSES = frozenset({401, 404})


class TelegramChannel:
    """Implements `sales_core.ports.MessagingChannel`.

    Like every channel here it distinguishes a request that certainly did not arrive (connection
    refused, 429, 5xx, a broken token: transient), one rejected for this message or person (the
    person blocked the bot, the chat does not exist: permanent) and one whose outcome is unknown
    (a timeout after the request left: an exception, so the Engine leaves the action in `SENDING`
    for a human). A text longer than Telegram accepts is sent in pieces; if a later piece fails
    after an earlier one arrived the outcome is partly unknown, and that too is an exception.
    """

    def __init__(
        self,
        *,
        token: str,
        client: httpx.Client | None = None,
        clock: Clock | None = None,
        api: TelegramApi | None = None,
    ) -> None:
        self._api = api if api is not None else TelegramApi(token, client=client)
        self._clock = clock

    @property
    def channel(self) -> Channel:
        return Channel.TELEGRAM

    def send(self, message: OutboundMessage) -> SendResult:
        chat_id = message.recipient.strip()
        if not chat_id.isdigit():
            return self._failure(
                SendStatus.FAILED_PERMANENT, "invalid_message", "the recipient is not a chat id"
            )
        text = message.draft.text
        if not text.strip():  # a WhatsApp template has no meaning here: there is nothing to say
            return self._failure(
                SendStatus.FAILED_PERMANENT, "invalid_message", "the message has no text"
            )
        first_id: str | None = None
        for position, piece in enumerate(chunks(text)):
            try:
                reply = self._api.send_text(chat_id, piece)
            except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                failure = self._failure(
                    SendStatus.FAILED_TRANSIENT, "connect_error", type(error).__name__
                )
                return self._partial_or(failure, position)
            # Any other transport error propagates: the outcome is unknown.
            if not reply.ok:
                return self._partial_or(self._rejected(reply), position)
            external = self._external_id(chat_id, reply.result)
            if position == 0:
                first_id = external
        return SendResult(status=SendStatus.ACCEPTED, timestamp=self._now(), external_id=first_id)

    # ---- answers ---------------------------------------------------------------------------

    def _rejected(self, reply: BotReply) -> SendResult:
        code = str(reply.error_code or reply.status)
        text = f"HTTP {reply.status}: {reply.description}"
        transient = reply.status == 429 or reply.status >= 500 or reply.status in _OPERATOR_STATUSES
        status = SendStatus.FAILED_TRANSIENT if transient else SendStatus.FAILED_PERMANENT
        if reply.status == 429 and reply.retry_after is not None:
            text += f" (retry after {reply.retry_after}s)"
        return self._failure(status, code, text)

    @staticmethod
    def _external_id(chat_id: str, result: object) -> str:
        try:
            return f"{chat_id}:{result['message_id']}"  # type: ignore[index]
        except (KeyError, TypeError) as error:
            # Accepted by HTTP status but the id is unreadable: do not guess, do not retry.
            raise RuntimeError(
                "Telegram accepted the message but the reply is unreadable"
            ) from error

    @staticmethod
    def _partial_or(result: SendResult, sent_pieces: int) -> SendResult:
        if sent_pieces == 0:
            return result
        # Part of the text is already with the person: neither "failed" nor "sent" is true.
        raise RuntimeError(
            f"a long message was delivered in part ({sent_pieces} piece(s)), then: "
            f"{result.error_message}"
        )

    def _failure(self, status: SendStatus, code: str, message: str) -> SendResult:
        return SendResult(
            status=status, timestamp=self._now(), error_code=code, error_message=message
        )

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else datetime.now(UTC)
