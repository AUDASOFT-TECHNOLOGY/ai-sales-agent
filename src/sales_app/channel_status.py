"""What a client's channel is, and what it still lacks: read from its settings, never from a secret.

Shared by the Client API (readiness, the channel routes) so both say the same thing. A password is
only ever asked about as "stored or not"; its value is never read here.
"""

from collections.abc import Mapping
from typing import Any

from sales_app.bootstrap import Application
from sales_app.config.channel_access import DEFAULT_ALLOWED
from sales_app.config.email_activation import ACTIVE, REJECTED, ActivationRecord
from sales_app.config.email_settings import (
    GMAIL_MODE,
    PLATFORM_MODE,
    GmailEmailSettings,
    PlatformEmailSettings,
    email_mode_of,
    parse_email_settings,
)
from sales_app.config.secrets import (
    EMAIL_PASSWORD,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_WEBHOOK_SECRET,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_VERIFY_TOKEN,
)
from sales_app.config.telegram_settings import TelegramChannelSettings, account_mode_of
from sales_app.mailbox_proof import READY as READY_MAILBOX
from sales_app.mailbox_proof import mailbox_state
from sales_app.secret_store import GLOBAL_SCOPE, SecretStoreError, client_scope

SENDER_NAME_PENDING = "Confirm your sender name to finish setting up your email."
AWAITING_ACTIVATION = "Revansa is activating your email. There is nothing for you to do."
ACTIVATION_REJECTED = "Your email activation request was not accepted. See the reason below."
_PREPARING = "Revansa is setting up this channel for you. There is nothing for you to do."
DEFAULT_CHANNEL = "whatsapp"
EMAIL = "email"
TELEGRAM = "telegram"
WHATSAPP = "whatsapp"


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
    return reason.startswith(("Email ", "Telegram ", "WhatsApp ")) or reason.endswith(
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


def activation_of(application: Application, client_id: str) -> ActivationRecord | None:
    """The client's e-mail activation request, if it made one."""
    service = getattr(application, "email_activation", None)
    if service is None:
        return None
    try:
        return service.get(client_id)
    except SecretStoreError:
        return None


def credential_stored(application: Application, client_id: str, key: str) -> bool:
    try:
        return application.credentials.status(client_scope(client_id), key).configured
    except SecretStoreError:
        return False


def password_stored(application: Application, client_id: str) -> bool:
    return credential_stored(application, client_id, EMAIL_PASSWORD)


READY = "ready"
WAITING_PLATFORM = (
    "waiting_platform"  # Revansa has to set something up: the client has nothing to do
)
ACTION_REQUIRED = "action_required"  # the client has to choose or fix something of its own

ChannelStatus = tuple[str, str | None]  # (READY | WAITING_PLATFORM | ACTION_REQUIRED, what to know)


def channel_status(
    application: Application, client_id: str, provider: str, document: Mapping[str, Any]
) -> ChannelStatus:
    """Where a channel stands for the client, in words that mention nothing technical.

    The connection of a channel (servers, mailbox, bot, webhook) is the platform's work: whatever is
    missing there is `WAITING_PLATFORM`, and the client has nothing to type. `ACTION_REQUIRED` is
    only for what the client itself decides (the channel it chose, how its messages are written).
    WhatsApp is connected by the Admin and has nothing for the client to do here: it is ready only
    once every Meta credential is stored."""
    if provider not in allowed_channels(application, client_id):
        return (
            ACTION_REQUIRED,
            "This channel is no longer available for your account. Choose another channel.",
        )
    if provider == TELEGRAM:
        return _telegram_status(application, client_id, document)
    if provider == WHATSAPP:
        return _whatsapp_status(application, client_id)
    if provider != EMAIL:
        return READY, None
    try:
        settings = parse_email_settings(channel_settings_of(document))
    except ValueError:
        if email_mode_of(channel_settings_of(document)) in (PLATFORM_MODE, GMAIL_MODE):
            return ACTION_REQUIRED, "Review how your messages are written, then save again."
        return WAITING_PLATFORM, _PREPARING
    if isinstance(settings, GmailEmailSettings):
        return _gmail_status(application, client_id)
    if isinstance(settings, PlatformEmailSettings):
        if not settings.from_name:
            # The one step that is the client's own: saving its Sender Name is the confirmation.
            return ACTION_REQUIRED, SENDER_NAME_PENDING
        record = activation_of(application, client_id)
        if record is not None and record.status != ACTIVE:
            if record.status == REJECTED:
                return ACTION_REQUIRED, ACTIVATION_REJECTED
            return WAITING_PLATFORM, AWAITING_ACTIVATION
        problem = _platform_mailbox_problem(application, client_id)
        return (WAITING_PLATFORM, problem) if problem else (READY, None)
    if not password_stored(application, client_id):
        return WAITING_PLATFORM, _PREPARING
    return READY, None


GMAIL_NOT_CONNECTED = "Connect your Gmail so Revansa can write to your prospects."
GMAIL_REAUTH = "Google needs you to authorise Revansa again. Reconnect your Gmail."
GMAIL_NO_CAP = "Your Gmail is connected. Sending starts once Revansa sets your daily sending limit."


def _gmail_status(application: Application, client_id: str) -> ChannelStatus:
    """Ready only with the platform's Google application set, a valid grant (the scope was checked
    when it was granted and at each renewal), and a daily send cap. Never a false ready."""
    from sales_app.send_cap_status import OK, REACHED, email_sending

    gmail = getattr(application, "gmail", None)
    if gmail is None or not gmail.configured():
        return WAITING_PLATFORM, _PREPARING
    state = gmail.connection.status(client_id).state
    if state == "not_connected":
        return ACTION_REQUIRED, GMAIL_NOT_CONNECTED
    if state == "reauth_required":
        return ACTION_REQUIRED, GMAIL_REAUTH
    if state != "connected":
        return WAITING_PLATFORM, _PREPARING
    # A limit of 1 or more is set when sending is `ok` or already used up for today (`reached`).
    if email_sending(application, client_id)["state"] not in (OK, REACHED):
        return WAITING_PLATFORM, GMAIL_NO_CAP
    return READY, None


def channel_problem(
    application: Application, client_id: str, provider: str, document: Mapping[str, Any]
) -> str | None:
    """Why the channel cannot run the agent yet, in words a client can read; None if it can."""
    status, message = channel_status(application, client_id, provider, document)
    return None if status == READY else message


def _platform_mailbox_problem(application: Application, client_id: str) -> str | None:
    """A mailbox Revansa provides is the administrator's to assign and to set up. It counts as ready
    only when the servers are set, the mailbox is assigned with its password AND both logins were
    accepted (`sales_app.mailbox_proof`). Whatever is missing, the client has nothing to type and
    is told to wait: never which step, never an address or a server."""
    return None if mailbox_state(application, client_id) == READY_MAILBOX else _PREPARING


TELEGRAM_NOT_CONNECTED = "Connect your Telegram account so Revansa can write to your prospects."
TELEGRAM_REAUTH = "Telegram needs you to sign in again. Reconnect your Telegram account."
TELEGRAM_LIMITED = (
    "Telegram limited your account for messages to people who did not expect them. Revansa sends "
    "no first message until the Revansa team lifts it; replies to people who write to you go on."
)


def _telegram_account_status(application: Application, client_id: str) -> ChannelStatus:
    """The client's own Telegram account: ready only with the platform's Telegram application set
    (and the library on the server), a session Telegram still honours, and a database for the daily
    count of new conversations. A limited account stays ready (replies go on), with the reason."""
    from sales_app.telegram_account import CONNECTED, NOT_CONNECTED, REAUTH_REQUIRED

    accounts = getattr(application, "telegram_accounts", None)
    if accounts is None or not accounts.configured() or application.send_limits is None:
        return WAITING_PLATFORM, _PREPARING
    status = accounts.status(client_id)
    if status.state == NOT_CONNECTED:
        return ACTION_REQUIRED, TELEGRAM_NOT_CONNECTED
    if status.state == REAUTH_REQUIRED:
        return ACTION_REQUIRED, TELEGRAM_REAUTH
    if status.state != CONNECTED:
        return WAITING_PLATFORM, _PREPARING
    return (READY, TELEGRAM_LIMITED) if status.limited else (READY, None)


def _telegram_status(
    application: Application, client_id: str, document: Mapping[str, Any]
) -> ChannelStatus:
    settings = channel_settings_of(document)
    try:
        TelegramChannelSettings.model_validate(settings)
    except ValueError:
        return ACTION_REQUIRED, "Review the messages your bot says first, then save again."
    if account_mode_of(settings):
        return _telegram_account_status(application, client_id)
    if not credential_stored(application, client_id, TELEGRAM_BOT_TOKEN) or not credential_stored(
        application, client_id, TELEGRAM_WEBHOOK_SECRET
    ):
        return WAITING_PLATFORM, _PREPARING
    return READY, None


WHATSAPP_NOT_CONNECTED = (
    "Connect your WhatsApp Business account so Revansa can write to your prospects."
)
WHATSAPP_REAUTH = "WhatsApp needs you to authorise Revansa again. Reconnect your WhatsApp account."
WHATSAPP_RECONNECT = "Your WhatsApp number changed. Connect the account of your new number."


def _whatsapp_status(application: Application, client_id: str) -> ChannelStatus:
    """The Meta credentials are all the Admin's: the platform's app secret and verify token, and the
    client's access token and phone number id. Stored is not verified (only Meta's own call to the
    webhook proves them), but a channel with one missing is never ready."""
    per_client = (WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID)
    platform = (WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN)
    for key in platform:
        try:
            stored = application.credentials.status(GLOBAL_SCOPE, key).configured
        except SecretStoreError:
            stored = False
        if not stored:
            return WAITING_PLATFORM, _PREPARING
    service = getattr(application, "whatsapp", None)
    status = service.status(client_id) if service is not None else None
    if status is not None and status.state == "reauth_required":
        return ACTION_REQUIRED, WHATSAPP_REAUTH
    profile = application.client_profiles.get(client_id)
    if profile is not None and profile.whatsapp_reconnect_required:
        # The number was changed: the account connected before is no longer the client's number.
        return ACTION_REQUIRED, WHATSAPP_RECONNECT
    if not all(credential_stored(application, client_id, key) for key in per_client):
        # Revansa's Meta app is set: the client connects its own account in one click. Without it,
        # the Admin connects the account by hand and the client waits.
        if service is not None and service.configured():
            return ACTION_REQUIRED, WHATSAPP_NOT_CONNECTED
        return WAITING_PLATFORM, _PREPARING
    return READY, None
