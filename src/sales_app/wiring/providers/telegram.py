"""The Telegram channel: a client's own bot, which prospects start and which then answers them.

One bot per client, never shared: the bot's token and the webhook secret are each client's own
(client scope, write-only). Telegram calls `POST /webhooks/telegram` with the secret it was given,
and the secret is what tells the clients apart; the bot's numeric id is the routing key.

**A bot cannot write first.** Telegram delivers a message only to a person who started the bot, so a
prospect from a spreadsheet or a CRM is unreachable until they do, and the Core waits for them
instead of failing them (`sales_core.safety.OPT_IN_CHANNELS`). They start it in one of two ways, both
verified by Telegram and neither needing the client to type anything by hand:

- the **invitation link** `t.me/<bot>?start=<signed id>`, one per prospect, which the client sends
  by whatever means it already has for that person (`GET /client/v1/channel/invitations`);
- the **phone button**: a person who writes to the bot is asked to share their own number, and it is
  matched to the prospect holding that number.

**The client's own account** (`mode: account`, `sales_app.telegram_account`) is the other way, and the
only one that can open a conversation: the client signs in with Telegram and Revansa writes from that
account, never to someone the client did not mark as expecting it (`consent_field`), never more than
`max_new_conversations_per_day` new conversations a day, and never again once Telegram limits the
account. Replies are read by polling the account (no webhook), for the client's prospects only.

Which clients may use the channel at all is the Admin's decision (`sales_app.config.channel_access`).
"""

import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from sales_app.config import ClientConfig
from sales_app.config.secrets import (
    TELEGRAM_API_HASH,
    TELEGRAM_API_ID,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_WEBHOOK_SECRET,
)
from sales_app.config.telegram_settings import (
    TelegramChannelSettings,
    account_mode_of,
    bot_id_of,
)
from sales_app.connection_tests import BotProbeFailure
from sales_app.secret_store import ScopeKind
from sales_app.send_cap import FirstContactCapChannel
from sales_app.wiring.providers.types import (
    ChannelSupport,
    CredentialDeclaration,
    ProviderDescriptor,
    ProviderKind,
    Requirement,
)
from sales_integrations.telegram import (
    BotCheck,
    BotCheckError,
    TelegramApi,
    TelegramChannel,
    TelegramWebhookParser,
)

log = logging.getLogger(__name__)

KEY = "telegram"
CHANNEL_KIND = "telegram"  # the Core's name for this channel
WEBHOOK_PATH = "/webhooks/telegram"


def _settings(config: ClientConfig) -> TelegramChannelSettings:
    return TelegramChannelSettings.model_validate(config.spec.channel_settings_for(KEY))


def is_account(config: ClientConfig) -> bool:
    """Whether this client writes from its own Telegram account rather than through a bot."""
    return account_mode_of(config.spec.channel_settings_for(KEY))


def build_channel(config: ClientConfig, *, accounts=None, first_contacts=None):
    """The bot's channel; or, in `account` mode, the client's own account behind the rationing of
    first messages. `accounts` is the application's `TelegramAccountService`; `first_contacts` gives
    the guard of a client's new conversations of the day (`guard(client_id, limit)`), and without it
    no first message is sent."""
    if not is_account(config):
        return TelegramChannel(token=config.secrets.get(TELEGRAM_BOT_TOKEN))
    if accounts is None:
        raise ValueError("this platform cannot write from a client's own Telegram account")
    settings = _settings(config)
    guard = (
        first_contacts(config.client_id, settings.max_new_conversations_per_day)
        if first_contacts is not None
        else None
    )
    return FirstContactCapChannel(accounts.channel(config), guard)


def build_inbox(config: ClientConfig, *, accounts=None):
    if accounts is None:
        raise ValueError("this platform cannot read a client's own Telegram account")
    return accounts.inbox(config)


def polls_inbox(config: ClientConfig) -> bool:
    return is_account(config)


def routing_key(config: ClientConfig) -> str | None:
    if is_account(config):
        return None  # no bot, no webhook
    return bot_id_of(config.secrets.get_optional(TELEGRAM_BOT_TOKEN))


def webhook_secret(config: ClientConfig) -> str | None:
    if is_account(config):
        return None
    return config.secrets.get_optional(TELEGRAM_WEBHOOK_SECRET)


def build_parser(app_secrets: Mapping[str, str], verify_token: str | None) -> TelegramWebhookParser:
    return TelegramWebhookParser(app_secrets=app_secrets, verify_token=verify_token)


def webhook_url(public_base_url: str | None) -> str | None:
    """Where Telegram must deliver, or None if the deployment has no usable public address (Telegram
    only delivers to https)."""
    if not public_base_url:
        return None
    parts = urlsplit(public_base_url.strip())
    if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
        return None
    return f"https://{parts.netloc}{parts.path.rstrip('/')}{WEBHOOK_PATH}"


# ---- a sender nobody matches ------------------------------------------------------------------


def contact_request(settings: TelegramChannelSettings) -> dict:
    """The keyboard with one button that shares the person's own phone number."""
    return {
        "keyboard": [[{"text": settings.contact_button, "request_contact": True}]],
        "one_time_keyboard": True,
        "resize_keyboard": True,
    }


def answer_unknown(config: ClientConfig, message: Any, *, api: TelegramApi | None = None) -> None:
    """Ask a stranger who they are: with a button to share their number, or, if they just did and
    no prospect has it, a sentence saying so. Nothing is promised and nothing is stored."""
    settings = _settings(config)
    api = api if api is not None else TelegramApi(config.secrets.get(TELEGRAM_BOT_TOKEN))
    if message.shared_phone:
        api.send_text(
            message.sender, settings.not_found_text, reply_markup={"remove_keyboard": True}
        )
    else:
        api.send_text(
            message.sender, settings.contact_prompt, reply_markup=contact_request(settings)
        )


# ---- the connection test ----------------------------------------------------------------------


class TelegramProbe:
    """What a connection test asks Telegram: who the bot is, and where it delivers. Sends no message
    to anyone. A failure is a `BotProbeFailure` kind and nothing else."""

    def __init__(self, token: str, *, check: BotCheck | None = None) -> None:
        self._check = check if check is not None else BotCheck(token)

    def check_bot(self) -> str:
        return self._translated(self._check.username)

    def register_webhook(self, url: str, secret: str) -> None:
        self._translated(lambda: self._check.register_webhook(url, secret))

    def webhook_state(self, expected_url: str | None) -> str:
        return self._translated(lambda: self._check.webhook_state(expected_url))

    def delete_webhook(self) -> None:
        self._translated(self._check.delete_webhook)

    @staticmethod
    def _translated(call):
        try:
            return call()
        except BotCheckError as error:
            raise BotProbeFailure(error.kind) from None


DESCRIPTOR = ProviderDescriptor(
    key=KEY,
    kind=ProviderKind.CHANNEL,
    label="Telegram",
    settings_models={ProviderKind.CHANNEL: TelegramChannelSettings},
    credentials=(
        CredentialDeclaration(
            key=TELEGRAM_BOT_TOKEN,
            label="Telegram bot token",
            scope=ScopeKind.CLIENT,
            required_when=Requirement.LIVE,
            pattern=re.compile(r"^\d{5,20}:[A-Za-z0-9_-]{30,64}$"),
            rule="must be the token BotFather gives (it looks like 123456789:AA...)",
        ),
        CredentialDeclaration(
            key=TELEGRAM_WEBHOOK_SECRET,
            label="Telegram webhook secret",
            scope=ScopeKind.CLIENT,
            required_when=Requirement.LIVE,
            pattern=re.compile(r"^[A-Za-z0-9_-]{32,256}$"),
            rule="must be 32 to 256 letters, digits, - or _",
        ),
        # The platform's own Telegram application: needed only by clients who write from their own
        # account (the sign-in is theirs, the application is Revansa's).
        CredentialDeclaration(
            key=TELEGRAM_API_ID,
            label="Telegram application id",
            scope=ScopeKind.GLOBAL,
            sensitive=False,
            pattern=re.compile(r"^[1-9]\d{3,11}$"),
            rule="must be the numeric api_id of my.telegram.org (digits only)",
        ),
        CredentialDeclaration(
            key=TELEGRAM_API_HASH,
            label="Telegram application hash",
            scope=ScopeKind.GLOBAL,
            pattern=re.compile(r"^[0-9a-fA-F]{32}$"),
            rule="must be the 32-character api_hash of my.telegram.org",
        ),
    ),
    # The token is what a connection is made with; the secret proves itself only when Telegram
    # calls the webhook.
    connection_keys=(TELEGRAM_BOT_TOKEN,),
    capabilities={
        "can_initiate": False,  # a bot: the person starts the conversation (see the docstring)
        # From the client's own account (`mode: account`): a first message only to a person the
        # client marked as expecting it, at most N new conversations a day.
        "can_initiate_from_account_with_consent": True,
        "reply_window_hours": None,
        "requires_template_outside_window": False,
        "inbound": "webhook",
    },
    build=build_channel,
    channel=ChannelSupport(
        kind=CHANNEL_KIND,
        routing_key=routing_key,
        webhook_secret=webhook_secret,
        build_parser=build_parser,
        answer_unknown=answer_unknown,
        build_inbox=build_inbox,
        polls_inbox=polls_inbox,
    ),
)
