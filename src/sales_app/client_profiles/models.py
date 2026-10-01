"""A client's account profile: who they log in as and the details they own.

Deliberately separate from `clients/<id>/client.yaml`, which is the client's *configuration* for the
Revansa engine. A profile is account data: the login, a hashed temporary password, the logo and the
WhatsApp number the client's sales agent uses. Stdlib only, like `sales_app.admin_auth.models`.
"""

import re
from dataclasses import dataclass
from datetime import datetime

LOGIN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._@+-]{2,63}$")


class ClientProfileError(Exception):
    """A client profile operation could not do what was asked."""


class ClientProfileStoreCorruptError(ClientProfileError):
    """Stored profile data is unreadable or inconsistent. Never guessed at."""


def normalize_login(login: str) -> str:
    return login.strip().lower()


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientProfile:
    """One client's account. `password_hash` is a bcrypt hash of a *temporary* password: the
    plain text is never stored. `whatsapp_number` is the business number (profile data), never Meta's
    phone number id or any credential."""

    client_id: str
    login: str
    password_hash: str
    must_change_password: bool = True
    logo: str | None = None  # a small `data:image/...;base64,` URL, validated by the Admin API
    whatsapp_number: str | None = None
    created_at: datetime
    created_by: str | None = None

    def __post_init__(self) -> None:
        if not self.client_id.strip():
            raise ValueError("client_id must not be empty")
        if not LOGIN_PATTERN.match(self.login):
            raise ValueError(f"invalid login {self.login!r}")
        if not self.password_hash:
            raise ValueError("password_hash must not be empty")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
