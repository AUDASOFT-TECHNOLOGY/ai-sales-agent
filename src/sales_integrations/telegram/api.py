"""The Telegram Bot API, one call at a time.

The bot token is part of every URL Telegram is called on, so nothing here puts a URL, a request or
a raw error in a message: Telegram's own `description` is kept, and the token is scrubbed from
anything that could still carry it.
"""

from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.telegram.org"
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_TEXT_CHARS = 4096  # what one `sendMessage` accepts


@dataclass(frozen=True, slots=True)
class BotReply:
    """What Telegram answered: the HTTP status, and its JSON envelope when it sent one."""

    status: int
    ok: bool
    result: Any = None
    error_code: int | None = None
    description: str = ""
    retry_after: int | None = None


class TelegramApi:
    """Calls the Bot API for one bot. A connection that could not be made raises
    `httpx.ConnectError`/`ConnectTimeout` (nothing was sent); any other transport error propagates,
    because then the request may have been delivered."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._token = token
        self._base = f"{base_url.rstrip('/')}/bot{token}"
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def __repr__(self) -> str:
        return "TelegramApi(<token redacted>)"

    def call(self, method: str, payload: dict[str, Any] | None = None) -> BotReply:
        response = self._client.post(f"{self._base}/{method}", json=payload or {})
        return self._reply(response)

    def scrub(self, text: str) -> str:
        """`text` without the token, for anything that may reach a log or an error."""
        return text.replace(self._token, "<token>") if self._token else text

    # ---- what the platform asks of a bot -------------------------------------------------

    def get_me(self) -> BotReply:
        return self.call("getMe")

    def webhook_info(self) -> BotReply:
        return self.call("getWebhookInfo")

    def set_webhook(self, url: str, secret_token: str) -> BotReply:
        return self.call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": ["message"],
                "drop_pending_updates": False,
            },
        )

    def delete_webhook(self) -> BotReply:
        """Stops Telegram delivering to us. Pending updates are kept, not dropped."""
        return self.call("deleteWebhook", {"drop_pending_updates": False})

    def send_text(
        self, chat_id: str, text: str, *, reply_markup: dict[str, Any] | None = None
    ) -> BotReply:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("sendMessage", payload)

    # ---- reading the answer ---------------------------------------------------------------

    def _reply(self, response: httpx.Response) -> BotReply:
        body: Any = None
        try:
            body = response.json()
        except ValueError:
            pass
        if not isinstance(body, dict):
            return BotReply(response.status_code, False, description=f"HTTP {response.status_code}")
        parameters = body.get("parameters")
        retry = parameters.get("retry_after") if isinstance(parameters, dict) else None
        code = body.get("error_code")
        return BotReply(
            status=response.status_code,
            ok=bool(body.get("ok")) and response.is_success,
            result=body.get("result"),
            error_code=code if isinstance(code, int) else None,
            description=self.scrub(str(body.get("description", "")))[:200],
            retry_after=retry if isinstance(retry, int) else None,
        )


def chunks(text: str, limit: int = MAX_TEXT_CHARS) -> list[str]:
    """`text` cut into pieces Telegram accepts, at a paragraph, line or word boundary where it
    can, never losing a word."""
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = max(rest.rfind(sep, 0, limit) for sep in ("\n\n", "\n", " "))
        cut = cut if cut > limit // 2 else limit
        pieces.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest or not pieces:
        pieces.append(rest)
    return pieces
