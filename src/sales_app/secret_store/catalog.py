"""What may be stored, and where.

A credential is identified by a *scope* and a logical *key*, never by an environment variable
name. The catalog is the only list of keys the store accepts; anything else is refused, so a typo
or a probing request cannot create arbitrary records.

Scopes: `global` (Revansa's own infrastructure) or `client:<client_id>` (one client's account).
`sensitive=False` marks a value that is an identifier or a setting rather than a secret (a phone
number id, an API version): it may be shown to an admin. A sensitive value never leaves the server.
"""

import re
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


_OPENAI_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
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
    return cleaned
