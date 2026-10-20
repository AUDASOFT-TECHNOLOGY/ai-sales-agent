"""Credentials resolved for one client, by logical key. Config files hold only names, never values.

A value comes from the credential store (`sales_app.secret_store`) first and from an environment
variable second (the older way, kept as a fallback so existing deployments keep working).
"""

from collections.abc import Mapping

# The logical keys the runtime asks for. The first six are the credential store's own keys (see
# `sales_app.secret_store.catalog`; a test keeps the two lists equal).
OPENAI_API_KEY = "openai.api_key"
OPENAI_ORGANIZATION = "openai.organization"  # optional
OPENAI_PROJECT = "openai.project"  # optional
OPENAI_BASE_URL = "openai.base_url"  # optional
OPENAI_MODEL_SOL = "openai.model_sol"  # which models Revansa offers; read by the Admin API
OPENAI_MODEL_LUNE = "openai.model_lune"  # legacy: only read while OPENAI_MODELS is not stored
OPENAI_MODELS = "openai.models"  # JSON list of {name, id, active}: the models Settings offers
WHATSAPP_ACCESS_TOKEN = "whatsapp.access_token"
WHATSAPP_PHONE_NUMBER_ID = "whatsapp.phone_number_id"
WHATSAPP_APP_SECRET = "whatsapp.app_secret"
WHATSAPP_API_VERSION = "whatsapp.api_version"
WHATSAPP_VERIFY_TOKEN = "whatsapp.verify_token"  # global; read when the parsers are built
# The platform's Meta app, for Embedded Signup (global, Admin only): the app id and the Login
# configuration id are public identifiers (the browser needs them); the app secret is the existing key.
WHATSAPP_APP_ID = "whatsapp.app_id"
WHATSAPP_CONFIG_ID = "whatsapp.config_id"
# One client's WhatsApp Business Account (client scope, not secret): written by the Embedded Signup
# completion or by the Admin; needed to manage message templates and the webhook subscription.
WHATSAPP_WABA_ID = "whatsapp.waba_id"
# Written only by the platform (never through the Admin credentials API: not in the catalog): "1"
# once Meta has refused the client's token, until the client connects again. Not a credential.
WHATSAPP_REAUTH_REQUIRED = "whatsapp.reauth_required"
# The state key that signs the Embedded Signup `state` is derived from the app secret (see
# `sales_app.whatsapp_service`); no extra credential to enter.
# Resolved from an environment variable only, and not in the credential store: the Google access
# model is going to change (OAuth per client), so nothing is stored for it in the meantime.
SHEETS_CREDENTIALS_FILE = "sheets.credentials_file"
# A client's own HubSpot Private App token (client scope). Stored write-only, and resolved into a
# client's secrets only when that client's source is HubSpot (see `SOURCE_CREDENTIALS`).
HUBSPOT_ACCESS_TOKEN = "hubspot.access_token"

# A client's own mailbox password (client scope), used for SMTP and IMAP alike. Stored write-only and
# resolved into a client's secrets only when that client's channel is e-mail.
EMAIL_PASSWORD = "email.password"
# The servers of the mailboxes Revansa provides (global, not secret; set by the Admin in Settings).
# Read only by the Admin's connection test for now: no client is resolved from them yet.
EMAIL_PLATFORM_DOMAIN = "email.platform_domain"
EMAIL_PLATFORM_SMTP_HOST = "email.platform_smtp_host"
EMAIL_PLATFORM_SMTP_PORT = "email.platform_smtp_port"
EMAIL_PLATFORM_SMTP_SECURITY = "email.platform_smtp_security"
EMAIL_PLATFORM_IMAP_HOST = "email.platform_imap_host"
EMAIL_PLATFORM_IMAP_PORT = "email.platform_imap_port"
EMAIL_PLATFORM_IMAP_SECURITY = "email.platform_imap_security"
# The platform's own test login for each server (global). Read only by the Admin's authentication
# test; never resolved into a client's secrets.
EMAIL_PLATFORM_SMTP_USERNAME = "email.platform_smtp_username"
EMAIL_PLATFORM_SMTP_PASSWORD = "email.platform_smtp_password"
EMAIL_PLATFORM_IMAP_USERNAME = "email.platform_imap_username"
EMAIL_PLATFORM_IMAP_PASSWORD = "email.platform_imap_password"
# The servers a platform mailbox needs, all of them (the domain is not needed to reach them).
EMAIL_PLATFORM_PROFILE_KEYS = (
    EMAIL_PLATFORM_SMTP_HOST,
    EMAIL_PLATFORM_SMTP_PORT,
    EMAIL_PLATFORM_SMTP_SECURITY,
    EMAIL_PLATFORM_IMAP_HOST,
    EMAIL_PLATFORM_IMAP_PORT,
    EMAIL_PLATFORM_IMAP_SECURITY,
)
# The mailbox Revansa assigned to a client (client scope, not secret, UNIQUE across clients): its
# address in lowercase. Its password is the client's `email.password`. Resolved into a client's
# secrets only when that client's e-mail mode is `platform`.
EMAIL_PLATFORM_MAILBOX = "email.platform_mailbox"
# One client's request to have its e-mail mailbox activated (client scope): a small JSON record the
# Admin works through by hand (see `sales_app.config.email_activation`). Stored beside the credentials
# but not a credential, and never resolved into a client's secrets.
EMAIL_ACTIVATION = "email.activation"
# "1" when the Admin decided that this client may be shown the login of its assigned mailbox (client
# scope, a flag and not a credential: never resolved into a client's secrets). Absent: the login is
# kept from the client, which is the default. See `sales_app.email_activation`.
EMAIL_CREDENTIALS_SHARED = "email.credentials_shared"
# A client's own Telegram bot (client scope), both write-only. The token is the bot's; the webhook
# secret is generated by Revansa when the bot is connected, tells Telegram's calls apart from anyone
# else's, and signs the invitation links. Resolved only when the client's channel is Telegram.
TELEGRAM_BOT_TOKEN = "telegram.bot_token"
TELEGRAM_WEBHOOK_SECRET = "telegram.webhook_secret"
# Telegram from a client's own account (MTProto). The platform's own Telegram application (global,
# Admin only, write-only): what lets each client sign in with its own account. Telegram's terms
# (API ToS 2.1) require the application to be registered by whoever operates the platform.
TELEGRAM_API_ID = "telegram.api_id"
TELEGRAM_API_HASH = "telegram.api_hash"
# One client's own account (client scope). Written only by the sign-in flow, never through the Admin
# credentials API (not in the credential catalog): the session (a FULL access key to the account),
# the account's numeric id (UNIQUE across clients), the mark that the session ended and the person
# must sign in again, and the mark that Telegram limited the account for unsolicited messages.
TELEGRAM_USER_SESSION = "telegram.user_session"
TELEGRAM_USER_ACCOUNT = "telegram.user_account"
TELEGRAM_USER_REAUTH = "telegram.user_reauth"
TELEGRAM_USER_LIMITED = "telegram.user_limited"
# Which channels clients may use (see `sales_app.config.channel_access`): the Admin's decision,
# stored beside the credentials but not credentials, and never resolved into a client's secrets.
CHANNELS_ALLOWED = "channels.allowed"  # global: "whatsapp,email" or "none"
CHANNELS_ACCESS = "channels.access"  # one client's exceptions: "+email,-whatsapp"
# The channels every client created from now on is given access to ("email" or "none"): the Admin's
# decision, applied once at creation as that client's own exception. Existing clients keep what
# they had.
CHANNELS_NEW_CLIENTS = "channels.new_clients"

# The platform's Google OAuth application (global, Admin only, write-only): the Gmail mailbox mode.
GMAIL_OAUTH_CLIENT_ID = "gmail.oauth_client_id"
GMAIL_OAUTH_CLIENT_SECRET = "gmail.oauth_client_secret"
GMAIL_OAUTH_STATE_KEY = "gmail.oauth_state_key"  # signs the OAuth `state`; at least 16 characters
# One client's Gmail grant (client scope). Written only by the OAuth callback, never through the
# Admin credentials API (they are not in the credential catalog): the refresh token, the connected
# address and the mark that Google needs the person to authorise again.
GMAIL_REFRESH_TOKEN = "gmail.refresh_token"
GMAIL_ACCOUNT_ADDRESS = "gmail.account_address"
GMAIL_REAUTH_REQUIRED = "gmail.reauth_required"
# When the mailbox was connected (ISO 8601, UTC; client scope, same rules): where the first read of the
# mailbox starts, so a reply that arrives before the first poll is not lost (spec 8.2).
GMAIL_CONNECTED_AT = "gmail.connected_at"

# One client's Google Sheets grant (client scope): the OAuth consent that lets Revansa read and write
# the client's own spreadsheet. Written only by the OAuth callback, never through the Admin credentials
# API (not in the credential catalog); no file, no service account. `SHEETS_OAUTH_GRANT` is not stored:
# it is resolved into a client's secrets ("1") when that client holds a usable grant.
SHEETS_REFRESH_TOKEN = "sheets.refresh_token"
SHEETS_ACCOUNT_ADDRESS = "sheets.account_address"
SHEETS_REAUTH_REQUIRED = "sheets.reauth_required"
SHEETS_OAUTH_GRANT = "sheets.oauth_grant"

# The platform's HubSpot application (a "public app" in a HubSpot developer account; global, Admin only,
# write-only): what lets each client connect its own HubSpot account by OAuth.
HUBSPOT_OAUTH_CLIENT_ID = "hubspot.oauth_client_id"
HUBSPOT_OAUTH_CLIENT_SECRET = "hubspot.oauth_client_secret"
HUBSPOT_OAUTH_STATE_KEY = (
    "hubspot.oauth_state_key"  # signs the OAuth `state`; at least 16 characters
)
# One client's HubSpot grant (client scope). Written only by the OAuth callback, never through the
# Admin credentials API (not in the credential catalog): the refresh token, the connected account and
# the mark that HubSpot needs the person to authorise again. `HUBSPOT_OAUTH_GRANT` is not stored: it is
# resolved into a client's secrets ("1") when that client holds a usable grant.
HUBSPOT_REFRESH_TOKEN = "hubspot.refresh_token"
HUBSPOT_ACCOUNT_ADDRESS = "hubspot.account_address"
HUBSPOT_REAUTH_REQUIRED = "hubspot.reauth_required"
HUBSPOT_OAUTH_GRANT = "hubspot.oauth_grant"

# The credential a client's own source needs, by the source's provider key: (key, label). Only a
# client that chose that source resolves it; every other client's secrets are what they were.
SOURCE_CREDENTIALS: dict[str, tuple[str, str]] = {
    "hubspot": (HUBSPOT_ACCESS_TOKEN, "HubSpot access token"),
}

# A CRM source whose client may also connect with its own authorisation: (refresh token key, reauth
# mark key, flag put in the client's secrets when the grant is usable), by the source's provider key.
SOURCE_GRANTS: dict[str, tuple[str, str, str]] = {
    "hubspot": (HUBSPOT_REFRESH_TOKEN, HUBSPOT_REAUTH_REQUIRED, HUBSPOT_OAUTH_GRANT),
}

_MIN_REDACTED_LENGTH = 8  # shorter values (an API version) would blank out ordinary words


class Secrets:
    """The values one client refers to. Never prints its contents."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def get(self, name: str) -> str:
        return self._values[name]

    def get_optional(self, name: str) -> str | None:
        return self._values.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._values

    def redact(self, text: str) -> str:
        """`text` with any value held here replaced, for messages that may reach an admin."""
        for value in sorted(self._values.values(), key=len, reverse=True):
            if len(value) >= _MIN_REDACTED_LENGTH:
                text = text.replace(value, "***")
        return text

    def __repr__(self) -> str:
        return f"Secrets(<{len(self._values)} values redacted>)"

    __str__ = __repr__
