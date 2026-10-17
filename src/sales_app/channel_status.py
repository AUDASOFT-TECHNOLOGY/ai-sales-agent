"""What a client's channel is, and what it still lacks: read from its settings, never from a secret.

Shared by the Client API (readiness, the channel routes) so both say the same thing. A password is
only ever asked about as "stored or not"; its value is never read here.
"""

from collections.abc import Mapping
from typing import Any

from sales_app.bootstrap import Application
from sales_app.config.channel_access import DEFAULT_ALLOWED
from sales_app.config.email_settings import PlatformEmailSettings, parse_email_settings
from sales_app.config.secrets import (
    EMAIL_PASSWORD,
    EMAIL_PLATFORM_MAILBOX,
    EMAIL_PLATFORM_PROFILE_KEYS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_WEBHOOK_SECRET,
)
from sales_app.config.telegram_settings import TelegramChannelSettings
from sales_app.secret_store import GLOBAL_SCOPE, SecretStoreError, client_scope

DEFAULT_CHANNEL = "whatsapp"
EMAIL = "email"
TELEGRAM = "telegram"


def channel_provider_of(raw: Mapping[str, Any], document: Mapping[str, Any]) -> str:
    """The chosen channel, else the operator's (`whatsapp.provider` in `client.yaml`), else
    WhatsApp."""
    section = document.get("channel")
    chosen = section.get("provider") if isinstance(section, Mapping) else None
    if isinstance(chosen, str) and chosen:
        return chosen
    whatsapp = raw.get("whatsapp")
    operator = whatsapp.get("provider") if isinstance(whatsapp, Mapping) else None
    return operator if isinstance(operator, str) and operator else DEFAULT_CHANNEL


def is_channel_reason(reason: str) -> bool:
    """Whether a "not ready" reason is about the channel, which a client sees as its own checks
    (with what to do) rather than as "contact your administrator"."""
    return reason.startswith(("Email ", "Telegram ")) or reason.endswith(
        "channel is not authorized for this account"
    )


def channel_settings_of(document: Mapping[str, Any]) -> dict[str, Any]:
    section = document.get("channel")
    settings = section.get("settings") if isinstance(section, Mapping) else None
    return dict(settings) if isinstance(settings, Mapping) else {}


def allowed_channels(application: Application, client_id: str) -> frozenset[str]:
    """What the Admin lets this client use now. Without a policy service, the default."""
    policy = application.channel_policy
    return policy.effective(client_id) if policy is not None else DEFAULT_ALLOWED


def credential_stored(application: Application, client_id: str, key: str) -> bool:
    try:
        return application.credentials.status(client_scope(client_id), key).configured
    except SecretStoreError:
        return False


def password_stored(application: Application, client_id: str) -> bool:
    return credential_stored(application, client_id, EMAIL_PASSWORD)


def channel_problem(
    application: Application, client_id: str, provider: str, document: Mapping[str, Any]
) -> str | None:
    """Why the channel cannot run the agent yet, in words a client can act on; None if it can.

    E-mail and Telegram have something for the client to do: WhatsApp is connected by the Admin."""
    if provider not in allowed_channels(application, client_id):
        return (
            "The channel you chose is not authorized for your account. Contact your administrator."
        )
    if provider == TELEGRAM:
        return _telegram_problem(application, client_id, document)
    if provider != EMAIL:
        return None
    try:
        settings = parse_email_settings(channel_settings_of(document))
    except ValueError:
        return "Your email settings are incomplete. Fill them in your channel settings."
    if isinstance(settings, PlatformEmailSettings):
        return _platform_mailbox_problem(application, client_id)
    if not password_stored(application, client_id):
        return "Your email password is missing. Add it in your channel settings."
    return None


def _platform_mailbox_problem(application: Application, client_id: str) -> str | None:
    """A mailbox Revansa provides is the administrator's to assign and to set up: whatever is
    missing, the client has nothing to type and is told to ask."""
    if not credential_stored(application, client_id, EMAIL_PLATFORM_MAILBOX):
        return "No mailbox has been assigned to your account yet. Contact your administrator."
    if not password_stored(application, client_id):
        return "Your assigned mailbox is not ready yet. Contact your administrator."
    try:
        servers_set = all(
            application.credentials.status(GLOBAL_SCOPE, key).configured
            for key in EMAIL_PLATFORM_PROFILE_KEYS
        )
    except SecretStoreError:
        servers_set = False
    if not servers_set:
        return "The email servers are not set up yet. Contact your administrator."
    return None


def _telegram_problem(
    application: Application, client_id: str, document: Mapping[str, Any]
) -> str | None:
    try:
        TelegramChannelSettings.model_validate(channel_settings_of(document))
    except ValueError:
        return "Your Telegram settings are incomplete. Fill them in your channel settings."
    if not credential_stored(application, client_id, TELEGRAM_BOT_TOKEN):
        return "Your Telegram bot token is missing. Add it in your channel settings."
    if not credential_stored(application, client_id, TELEGRAM_WEBHOOK_SECRET):
        return "Your Telegram bot is not connected yet. Connect it in your channel settings."
    return None
