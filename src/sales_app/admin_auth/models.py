"""Admin accounts, sessions and roles: who may reach `/admin/v1` and for which clients.

Stdlib only, like `sales_app.config_store.models`: what a store backend looks like is decided
elsewhere, so any backend (file now, a database later) can hold these records unchanged.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class AdminAuthError(Exception):
    """An admin auth operation could not do what was asked."""


class AdminStoreError(AdminAuthError):
    """Stored admin data (accounts or sessions) could not be read or written."""


class AdminStoreCorruptError(AdminStoreError):
    """Stored data is unreadable, edited by hand, or inconsistent. Never guessed at."""


class InvalidCredentialsError(AdminAuthError):
    """A login attempt did not match an active account."""


class TooManyAttemptsError(AdminAuthError):
    """Too many recent failed logins for this username; try again later."""


class AdminRole(str, Enum):
    OWNER = "owner"
    OPERATOR = "operator"
    EDITOR = "editor"
    VIEWER = "viewer"


@dataclass(frozen=True, slots=True, kw_only=True)
class AdminUser:
    """One admin account.

    `client_ids` is the explicit allow-list every role but OWNER is bound to: no role other
    than OWNER grants an implicit "every client" scope, even `operator`. See `can_reach`.
    """

    id: str
    username: str
    password_hash: str
    role: AdminRole
    client_ids: frozenset[str] = frozenset()
    active: bool = True
    created_at: datetime
    created_by: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("id must not be empty")
        if not self.username.strip():
            raise ValueError("username must not be empty")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

    def can_reach(self, client_id: str) -> bool:
        return self.role is AdminRole.OWNER or client_id in self.client_ids


@dataclass(frozen=True, slots=True, kw_only=True)
class AdminSession:
    """One logged-in session, looked up by the SHA-256 hash of its opaque token. The raw token
    is never stored (see `new_token` / `hash_token`)."""

    token_hash: str
    admin_id: str
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if len(self.token_hash) != 64 or any(c not in "0123456789abcdef" for c in self.token_hash):
            raise ValueError("token_hash must be a sha256 hex digest")
        if not self.admin_id.strip():
            raise ValueError("admin_id must not be empty")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


def new_token() -> str:
    """A fresh opaque session token: 256 bits, URL-safe. Returned to the caller once; only its
    hash is ever stored."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
