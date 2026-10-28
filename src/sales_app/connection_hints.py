"""What a client says about the account it wants to connect, kept before Revansa can connect it.

A connection needs two things: Revansa has finished its side (the platform's Google, Meta, Telegram or
HubSpot application, and the channel opened for the client) and the client has done its own (the account
it chooses and the permission it gives on the provider's page). The two do not wait for each other in
this order: a client can state *which* Gmail, Telegram number, Google account or HubSpot account it
wants to use as soon as it logs in. That statement is kept here, in the client's own scope of the
encrypted secret store, and is used as a suggestion when the provider's page opens (the Gmail
`login_hint`) or when the code is asked for (the Telegram number).

A hint is never a credential and proves nothing: "connected" is still only said when the provider has
accepted the client's own authorisation. WhatsApp is not here: its number is the client's profile.
"""

import re
from typing import Final

from sales_app.secret_store import SecretStore, client_scope

KINDS: Final = ("gmail", "sheets", "hubspot", "telegram")
EMAIL_KINDS: Final = frozenset({"gmail", "sheets", "hubspot"})

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
_PHONE = re.compile(r"^\+?[0-9][0-9 ()\-]{5,24}$")


class InvalidHintError(ValueError):
    """The value is not what that kind of hint holds. The value is never part of the message."""


def clean(kind: str, value: str) -> str:
    """The value as it is kept, or `InvalidHintError`."""
    text = value.strip()
    if kind in EMAIL_KINDS:
        if not _EMAIL.match(text) or len(text) > 254:
            raise InvalidHintError(f"{kind} expects an e-mail address")
        return text.lower()
    if kind == "telegram":
        if not _PHONE.match(text):
            raise InvalidHintError("telegram expects a phone number with its country code")
        return re.sub(r"[ ()\-]", "", text)
    raise InvalidHintError("unknown connection")


def _key(kind: str) -> str:
    return f"connection.hint.{kind}"


class ConnectionHints:
    def __init__(self, secrets: SecretStore) -> None:
        self._secrets = secrets

    def get(self, client_id: str, kind: str) -> str | None:
        if kind not in KINDS:
            return None
        return self._secrets.get(client_scope(client_id), _key(kind))

    def all(self, client_id: str) -> dict[str, str | None]:
        return {kind: self.get(client_id, kind) for kind in KINDS}

    def set(self, client_id: str, kind: str, value: str) -> str:
        kept = clean(kind, value)
        self._secrets.set(client_scope(client_id), _key(kind), kept, by=f"client:{client_id}")
        return kept

    def clear(self, client_id: str, kind: str) -> None:
        if kind in KINDS:
            self._secrets.delete(client_scope(client_id), _key(kind))
