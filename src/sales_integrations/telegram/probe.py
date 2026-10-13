"""What a connection check asks Telegram about a bot: who it is, and where it delivers.

Nothing is sent to anyone. A failure is a kind (`auth`, `unreachable`, `rate_limited`, `rejected` or
`service_error`) and nothing else: not what Telegram said, and never the token.
"""

from collections.abc import Callable

import httpx

from sales_integrations.telegram.api import BotReply, TelegramApi


class BotCheckError(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class BotCheck:
    def __init__(self, token: str, *, client: httpx.Client | None = None) -> None:
        self._api = TelegramApi(token, client=client)

    def username(self) -> str:
        """The bot's username (empty if Telegram did not give one)."""
        reply = self._ask(self._api.get_me)
        username = reply.result.get("username") if isinstance(reply.result, dict) else None
        return username if isinstance(username, str) else ""

    def register_webhook(self, url: str, secret: str) -> None:
        """Tell Telegram to deliver this bot's messages to `url`, authenticated by `secret`."""
        reply = self._ask(lambda: self._api.set_webhook(url, secret), rejects_as="rejected")
        if reply.result is not True:
            raise BotCheckError("service_error")

    def webhook_state(self, expected_url: str | None) -> str:
        """`ok`, `not_set`, `other_url` or `failing`."""
        reply = self._ask(self._api.webhook_info)
        info = reply.result if isinstance(reply.result, dict) else {}
        url = info.get("url") or ""
        if not url:
            return "not_set"
        if expected_url is not None and url != expected_url:
            return "other_url"
        if info.get("last_error_date") and info.get("pending_update_count", 0):
            return "failing"
        return "ok"

    @staticmethod
    def _ask(call: Callable[[], BotReply], *, rejects_as: str = "service_error") -> BotReply:
        try:
            reply = call()
        except httpx.TransportError:
            raise BotCheckError("unreachable") from None
        if reply.ok:
            return reply
        if reply.status in (401, 404):
            raise BotCheckError("auth")
        if reply.status == 429:
            raise BotCheckError("rate_limited")
        if reply.status == 400:
            raise BotCheckError(rejects_as)
        raise BotCheckError("service_error")
