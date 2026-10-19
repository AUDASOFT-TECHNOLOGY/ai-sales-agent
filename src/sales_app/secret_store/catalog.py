"""What may be stored, and where.

A credential is identified by a *scope* and a logical *key*, never by an environment variable
name. The catalog is the only list of keys the store accepts; anything else is refused, so a typo
or a probing request cannot create arbitrary records.

Scopes: `global` (Revansa's own infrastructure) or `client:<client_id>` (one client's account).
`sensitive=False` marks a value that is an identifier or a setting rather than a secret (a phone
number id, an API version): it may be shown to an admin. A sensitive value never leaves the server.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from sales_app.config_store.models import CLIENT_ID_PATTERN
from sales_app.secret_store.errors import InvalidCredentialError, UnknownCredentialError

GLOBAL_SCOPE = "global"
_CLIENT_PREFIX = "client:"
MAX_VALUE_LENGTH = 4096


class ScopeKind(StrEnum):
    GLOBAL = "global"
    CLIENT = "client"


def client_scope(client_id: str) -> str:
    if not CLIENT_ID_PATTERN.match(client_id):
        raise ValueError(f"invalid client id {client_id!r}")
    return f"{_CLIENT_PREFIX}{client_id}"


def scope_kind(scope: str) -> ScopeKind:
    """The kind of a scope string; raises ValueError for anything that is not a valid scope."""
    if scope == GLOBAL_SCOPE:
        return ScopeKind.GLOBAL
    if scope.startswith(_CLIENT_PREFIX) and CLIENT_ID_PATTERN.match(scope[len(_CLIENT_PREFIX) :]):
        return ScopeKind.CLIENT
    raise ValueError(f"invalid scope {scope!r}")


@dataclass(frozen=True, slots=True)
class CredentialSpec:
    key: str
    scope_kind: ScopeKind
    label: str
    sensitive: bool = True
    unique: bool = False  # no two scopes may hold the same value
    pattern: re.Pattern[str] | None = None  # the whole (stripped) value must match
    rule: str = ""  # what `pattern` expects, in words (shown when a value is refused)
    # The form a value is stored in, applied after the pattern is checked. Only for a value that
    # `unique` compares: two spellings of one thing must become one value to be told apart.
    canonical: Callable[[str], str] | None = None


_OPENAI_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
# The platform mail server profile (Admin, global): settings of Revansa's own mail servers, not
# secrets. The limits are the ones a client's mailbox settings have (`config.email_settings`).
_MAIL_HOST = re.compile(r"^(?=.{3,253}$)[a-z0-9]([a-z0-9.-]*[a-z0-9])?$", re.IGNORECASE)
_MAIL_DOMAIN = re.compile(
    r"^(?=.{4,253}$)[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", re.IGNORECASE
)
_SMTP_PORT = re.compile(r"^(25|465|587|2525)$")
_IMAP_PORT = re.compile(r"^(143|993)$")
_MAIL_SECURITY = re.compile(r"^(starttls|ssl)$")
_MAIL_LOGIN = re.compile(r"^[^\s<>,;\"'()\[\]\\]{1,254}$")
_LOGIN_RULE = "must be a login (an address or a name, without spaces)"
_HOST_RULE = "must be a host name (letters, digits, dots and hyphens)"
_SECURITY_RULE = "must be starttls or ssl"
# The address of a mailbox Revansa assigns to a client (`email.platform_mailbox`). The same rules as
# the address of a client's own mailbox (`config.email_settings`), in any case: it is stored in
# lowercase (`canonical`), so that two spellings of one address are one value, and the store can
# refuse the second one atomically.
MAILBOX_ADDRESS = re.compile(
    r"^(?=.{6,254}$)[^@\s<>,;\"'()\[\]\\]+@[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$",
    re.IGNORECASE,
)
MAILBOX_ADDRESS_RULE = "must be an email address such as name@example.com"

_HUBSPOT_TOKEN = re.compile(
    r"^pat-[a-z]{2,4}\d{1,2}-[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"
)

CATALOG: tuple[CredentialSpec, ...] = (
    # ---- Revansa (global) ---------------------------------------------------------------
    CredentialSpec("openai.api_key", ScopeKind.GLOBAL, "OpenAI API key"),
    CredentialSpec(
        "openai.organization",
        ScopeKind.GLOBAL,
        "OpenAI organization",
        sensitive=False,
        pattern=_OPENAI_ID,
        rule="must be an OpenAI id (letters, digits, - _ . only)",
    ),
    CredentialSpec(
        "openai.project",
        ScopeKind.GLOBAL,
        "OpenAI project",
        sensitive=False,
        pattern=_OPENAI_ID,
        rule="must be an OpenAI id (letters, digits, - _ . only)",
    ),
    CredentialSpec(
        "openai.base_url",
        ScopeKind.GLOBAL,
        "OpenAI base URL",
        sensitive=False,
        pattern=re.compile(r"^https://[^\s/?#@]+(/[^\s?#]*)?$"),
        rule="must be an https URL without credentials, e.g. https://api.openai.com/v1",
    ),
    # The model ids Revansa offers for OpenAI ("GPT Sol", "GPT Lune"). Not defined by the code:
    # the Admin enters the exact id the OpenAI account serves.
    CredentialSpec(
        "openai.model_sol",
        ScopeKind.GLOBAL,
        "GPT Sol model id",
        sensitive=False,
        pattern=_MODEL_ID,
        rule="must be a model id (letters, digits, - _ . : only)",
    ),
    CredentialSpec(
        "openai.model_lune",
        ScopeKind.GLOBAL,
        "GPT Lune model id",
        sensitive=False,
        pattern=_MODEL_ID,
        rule="must be a model id (letters, digits, - _ . : only)",
    ),
    # The model list Settings manages: a JSON array of {name, id, active}, validated by
    # `sales_app.llm_models` (the two keys above are only read until this one is stored).
    CredentialSpec("openai.models", ScopeKind.GLOBAL, "OpenAI models", sensitive=False),
    CredentialSpec("whatsapp.verify_token", ScopeKind.GLOBAL, "WhatsApp verify token"),
    CredentialSpec("whatsapp.app_secret", ScopeKind.GLOBAL, "Meta app secret"),
    CredentialSpec(
        "whatsapp.api_version",
        ScopeKind.GLOBAL,
        "WhatsApp API version",
        sensitive=False,
        pattern=re.compile(r"^v\d{1,3}\.\d{1,3}$"),
        rule="must look like v21.0",
    ),
    # The shared secret that signs payment confirmations (`sales_app.payment_provider`). Setting it
    # is what turns the payment webhook on; it is never returned.
    CredentialSpec(
        "payments.webhook_secret",
        ScopeKind.GLOBAL,
        "Payment webhook signing secret",
        pattern=re.compile(r"^[\x21-\x7e]{16,256}$"),
        rule="must be 16 to 256 printable characters without spaces",
    ),
    # The platform's Google OAuth application, for the clients who connect their own Gmail. Setting
    # them is what turns Gmail on; they are never returned. Stored is not verified by Google.
    CredentialSpec(
        "gmail.oauth_client_id",
        ScopeKind.GLOBAL,
        "Google OAuth client id",
        pattern=re.compile(r"^[!-~]{10,256}$"),
        rule="must be 10 to 256 printable characters without spaces",
    ),
    CredentialSpec(
        "gmail.oauth_client_secret",
        ScopeKind.GLOBAL,
        "Google OAuth client secret",
        pattern=re.compile(r"^[!-~]{8,256}$"),
        rule="must be 8 to 256 printable characters without spaces",
    ),
    CredentialSpec(
        "gmail.oauth_state_key",
        ScopeKind.GLOBAL,
        "Google OAuth state signing key",
        pattern=re.compile(r"^[!-~]{16,256}$"),
        rule="must be 16 to 256 printable characters without spaces",
    ),
    # The platform's HubSpot application, for the clients who connect their own HubSpot account.
    # Setting them is what turns the HubSpot connection on; they are never returned.
    CredentialSpec(
        "hubspot.oauth_client_id",
        ScopeKind.GLOBAL,
        "HubSpot app client id",
        pattern=re.compile(r"^[!-~]{8,256}$"),
        rule="must be 8 to 256 printable characters without spaces",
    ),
    CredentialSpec(
        "hubspot.oauth_client_secret",
        ScopeKind.GLOBAL,
        "HubSpot app client secret",
        pattern=re.compile(r"^[!-~]{8,256}$"),
        rule="must be 8 to 256 printable characters without spaces",
    ),
    CredentialSpec(
        "hubspot.oauth_state_key",
        ScopeKind.GLOBAL,
        "HubSpot OAuth state signing key",
        pattern=re.compile(r"^[!-~]{16,256}$"),
        rule="must be 16 to 256 printable characters without spaces",
    ),
    # The servers of the mailboxes Revansa provides (OVH today). Only the servers: which mailbox a
    # client gets, and its password, are the client's own (`email.password`, client scope).
    CredentialSpec(
        "email.platform_domain",
        ScopeKind.GLOBAL,
        "Platform mail domain",
        sensitive=False,
        pattern=_MAIL_DOMAIN,
        rule="must be a domain name such as mail.example.com",
    ),
    CredentialSpec(
        "email.platform_smtp_host",
        ScopeKind.GLOBAL,
        "Platform SMTP host",
        sensitive=False,
        pattern=_MAIL_HOST,
        rule=_HOST_RULE,
    ),
    CredentialSpec(
        "email.platform_smtp_port",
        ScopeKind.GLOBAL,
        "Platform SMTP port",
        sensitive=False,
        pattern=_SMTP_PORT,
        rule="must be 25, 465, 587 or 2525",
    ),
    CredentialSpec(
        "email.platform_smtp_security",
        ScopeKind.GLOBAL,
        "Platform SMTP security",
        sensitive=False,
        pattern=_MAIL_SECURITY,
        rule=_SECURITY_RULE,
    ),
    CredentialSpec(
        "email.platform_imap_host",
        ScopeKind.GLOBAL,
        "Platform IMAP host",
        sensitive=False,
        pattern=_MAIL_HOST,
        rule=_HOST_RULE,
    ),
    CredentialSpec(
        "email.platform_imap_port",
        ScopeKind.GLOBAL,
        "Platform IMAP port",
        sensitive=False,
        pattern=_IMAP_PORT,
        rule="must be 143 or 993",
    ),
    CredentialSpec(
        "email.platform_imap_security",
        ScopeKind.GLOBAL,
        "Platform IMAP security",
        sensitive=False,
        pattern=_MAIL_SECURITY,
        rule=_SECURITY_RULE,
    ),
    # The login the Admin gives to check that each server really accepts an authentication. It is a
    # test account of the platform: no client ever receives it (a client's mailbox has its own
    # address and its own password), and it is not part of the profile a mailbox needs.
    CredentialSpec(
        "email.platform_smtp_username",
        ScopeKind.GLOBAL,
        "Platform SMTP username",
        sensitive=False,
        pattern=_MAIL_LOGIN,
        rule=_LOGIN_RULE,
    ),
    CredentialSpec("email.platform_smtp_password", ScopeKind.GLOBAL, "Platform SMTP password"),
    CredentialSpec(
        "email.platform_imap_username",
        ScopeKind.GLOBAL,
        "Platform IMAP username",
        sensitive=False,
        pattern=_MAIL_LOGIN,
        rule=_LOGIN_RULE,
    ),
    CredentialSpec("email.platform_imap_password", ScopeKind.GLOBAL, "Platform IMAP password"),
    # ---- one client ---------------------------------------------------------------------
    CredentialSpec("whatsapp.access_token", ScopeKind.CLIENT, "WhatsApp access token"),
    CredentialSpec(
        "whatsapp.phone_number_id",
        ScopeKind.CLIENT,
        "WhatsApp phone number id",
        sensitive=False,
        unique=True,
        pattern=re.compile(r"^\d{5,32}$"),
        rule="must be the numeric id Meta gives the phone number (digits only)",
    ),
    CredentialSpec("email.password", ScopeKind.CLIENT, "Email password"),
    # The mailbox Revansa assigned to this client (e-mail mode `platform`). Not a secret, and held
    # by one client only: the store refuses the same address for a second one.
    CredentialSpec(
        "email.platform_mailbox",
        ScopeKind.CLIENT,
        "Email mailbox",
        sensitive=False,
        unique=True,
        pattern=MAILBOX_ADDRESS,
        rule=MAILBOX_ADDRESS_RULE,
        canonical=str.lower,
    ),
    CredentialSpec(
        "telegram.bot_token",
        ScopeKind.CLIENT,
        "Telegram bot token",
        pattern=re.compile(r"^\d{5,20}:[A-Za-z0-9_-]{30,64}$"),
        rule="must be the token BotFather gives (it looks like 123456789:AA...)",
    ),
    CredentialSpec(
        "telegram.webhook_secret",
        ScopeKind.CLIENT,
        "Telegram webhook secret",
        pattern=re.compile(r"^[A-Za-z0-9_-]{32,256}$"),
        rule="must be 32 to 256 letters, digits, - or _",
    ),
    # The platform's own Telegram application (my.telegram.org), for the clients who write from their
    # own Telegram account. Setting both is what turns that mode on; never returned.
    CredentialSpec(
        "telegram.api_id",
        ScopeKind.GLOBAL,
        "Telegram application id",
        sensitive=False,
        pattern=re.compile(r"^[1-9]\d{3,11}$"),
        rule="must be the numeric api_id of my.telegram.org (digits only)",
    ),
    CredentialSpec(
        "telegram.api_hash",
        ScopeKind.GLOBAL,
        "Telegram application hash",
        pattern=re.compile(r"^[0-9a-fA-F]{32}$"),
        rule="must be the 32-character api_hash of my.telegram.org",
    ),
    CredentialSpec(
        "hubspot.access_token",
        ScopeKind.CLIENT,
        "HubSpot access token",
        unique=True,
        pattern=_HUBSPOT_TOKEN,
        rule="must be a HubSpot Private App access token (it looks like pat-eu1-...)",
    ),
)
_BY_KEY = {spec.key: spec for spec in CATALOG}


def spec_for(scope: str, key: str) -> CredentialSpec:
    """The catalog entry for `key` in `scope`, or UnknownCredentialError."""
    spec = _BY_KEY.get(key)
    if spec is None or spec.scope_kind is not scope_kind(scope):
        raise UnknownCredentialError(f"unknown credential {key!r} for this scope")
    return spec


def specs_for(kind: ScopeKind) -> tuple[CredentialSpec, ...]:
    return tuple(spec for spec in CATALOG if spec.scope_kind is kind)


def normalize_value(spec: CredentialSpec, value: str) -> str:
    """The value to store, or InvalidCredentialError. Messages describe the rule, not the value."""
    cleaned = value.strip()  # a pasted token often carries a trailing newline
    if not cleaned:
        raise InvalidCredentialError("value must not be empty")
    if len(cleaned) > MAX_VALUE_LENGTH:
        raise InvalidCredentialError(f"value is longer than {MAX_VALUE_LENGTH} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        raise InvalidCredentialError("value must not contain control characters or line breaks")
    if spec.pattern is not None and not spec.pattern.match(cleaned):
        raise InvalidCredentialError(f"{spec.label} {spec.rule}")
    return spec.canonical(cleaned) if spec.canonical is not None else cleaned
