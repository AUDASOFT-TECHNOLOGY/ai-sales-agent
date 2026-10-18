"""Connecting a client's Telegram bot to the platform: the platform's work, never the client's.

The bot's token is a credential written by the Admin (`telegram.bot_token`, client scope). Connecting
tells Telegram to deliver that bot's messages to `<public address>/webhooks/telegram` with a secret
only the platform and Telegram know. The secret is stored (write-only) only once Telegram agreed, so
"connected" is never said before it is true. Safe to repeat: the secret, and the invitation links
signed under it, stay.
"""

import secrets
from collections.abc import Callable

from sales_app.bootstrap import Application
from sales_app.config.secrets import TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET
from sales_app.config.telegram_settings import bot_id_of
from sales_app.connection_tests import (
    _BOT_PROBLEMS,
    BotProbe,
    BotProbeFailure,
    ConnectionResult,
    check_telegram,
)
from sales_app.secret_store import SecretStoreError, client_scope
from sales_app.wiring.providers import telegram as telegram_provider

BotProbeFactory = Callable[[str], BotProbe]  # from the bot token


class TelegramConnectionError(Exception):
    """The bot cannot be connected, for a reason the caller answers with `status`. Never carries a
    token or a secret."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class _Failed:
    """A probe that is already known to have failed, so the same sentence is told as for a test."""

    def __init__(self, failure: BotProbeFailure) -> None:
        self._failure = failure

    def check_bot(self) -> str:
        raise self._failure

    def register_webhook(self, url: str, secret: str) -> None:
        raise self._failure

    def webhook_state(self, expected_url: str | None) -> str:
        raise self._failure


def connect_bot(
    application: Application, client_id: str, bot_probe: BotProbeFactory, *, actor: str
) -> ConnectionResult:
    """Register the client's bot with Telegram. Raises `TelegramConnectionError` when it cannot even
    be tried (no token, no public address, a bot that belongs to another client, storage down)."""
    try:
        token = application.credentials.reveal(client_scope(client_id), TELEGRAM_BOT_TOKEN)
    except SecretStoreError:
        raise TelegramConnectionError(
            503, "credential storage is temporarily unavailable"
        ) from None
    if not token:
        raise TelegramConnectionError(409, "this client has no Telegram bot token yet")
    url = telegram_provider.webhook_url(application.settings.public_base_url)
    if url is None:
        raise TelegramConnectionError(
            409, "the platform has no public https address to receive Telegram messages on"
        )
    bot = bot_id_of(token)
    for config in application.registry.configs():
        if config.client_id != client_id and config.telegram_bot_id == bot:
            raise TelegramConnectionError(409, "this bot cannot be used for this account")
    probe = bot_probe(token)
    try:
        existing = application.credentials.reveal(client_scope(client_id), TELEGRAM_WEBHOOK_SECRET)
    except SecretStoreError:
        raise TelegramConnectionError(
            503, "credential storage is temporarily unavailable"
        ) from None
    secret = existing or secrets.token_urlsafe(32)
    try:
        probe.check_bot()
        probe.register_webhook(url, secret)
    except BotProbeFailure as failure:
        return check_telegram(probe=_Failed(failure), expected_url=url)
    try:
        application.credentials.save(
            client_scope(client_id), TELEGRAM_WEBHOOK_SECRET, secret, actor=actor, apply=True
        )
    except SecretStoreError:
        raise TelegramConnectionError(
            503, "credential storage is temporarily unavailable"
        ) from None
    return check_telegram(probe=probe, expected_url=url)


REAL_CHECK = {"check": "real_telegram_api"}  # every answer below came from Telegram, none simulated


def _stored_token(application: Application, client_id: str) -> str | None:
    try:
        return application.credentials.reveal(client_scope(client_id), TELEGRAM_BOT_TOKEN)
    except SecretStoreError:
        raise TelegramConnectionError(
            503, "credential storage is temporarily unavailable"
        ) from None


def check_bot_connection(
    application: Application, client_id: str, bot_probe: BotProbeFactory
) -> ConnectionResult:
    """The Admin's test: asks Telegram (getMe, getWebhookInfo) who the bot is and where it delivers.
    Read-only, sends no message, changes nothing. Always a real call to Telegram; the result says so
    in `details["check"]`."""
    token = _stored_token(application, client_id)
    url = telegram_provider.webhook_url(application.settings.public_base_url)
    result = check_telegram(probe=bot_probe(token) if token else None, expected_url=url)
    return ConnectionResult(result.ok, result.message, {**result.details, **REAL_CHECK})


def disconnect_bot(
    application: Application, client_id: str, bot_probe: BotProbeFactory, *, actor: str
) -> ConnectionResult:
    """Stops Telegram delivering this client's bot to the platform and forgets the webhook secret,
    so the client goes back to waiting for the platform. The bot token stays (the Admin removes it
    separately). The secret is forgotten only once Telegram stopped delivering, or when Telegram no
    longer knows the token (nothing then can be delivered): "disconnected" is never said earlier."""
    token = _stored_token(application, client_id)
    details = dict(REAL_CHECK)
    if token:
        try:
            bot_probe(token).delete_webhook()  # type: ignore[attr-defined]
        except BotProbeFailure as failure:
            if failure.kind != "auth":
                message = _BOT_PROBLEMS.get(failure.kind, "it failed")
                return ConnectionResult(
                    False,
                    f"Telegram: {message}; the bot is still connected.",
                    {**details, "bot": failure.kind},
                )
            details["bot"] = "auth"
        else:
            details["bot"] = "ok"
    else:
        details["bot"] = "no_token"
    try:
        application.credentials.remove(
            client_scope(client_id), TELEGRAM_WEBHOOK_SECRET, actor=actor
        )
    except SecretStoreError:
        raise TelegramConnectionError(
            503, "credential storage is temporarily unavailable"
        ) from None
    return ConnectionResult(True, "Telegram: the bot is disconnected from the platform.", details)
