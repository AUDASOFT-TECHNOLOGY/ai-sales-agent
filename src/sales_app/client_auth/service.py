"""Client login, sessions and password change.

The Client counterpart of `sales_app.admin_auth.service`, built from the same parts (bcrypt
hashes, opaque hashed session tokens, the sliding-window login guard) over `ClientProfileStore`.
A session is stored with `AdminSession`, whose `admin_id` field holds the *client id* here; the
session files live in their own directory, so an Admin token and a Client token never meet.

The client id always comes from the session, never from the caller: `authenticate` is the only way
to learn who is logged in. Knows nothing about HTTP.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta

from sales_app.admin_auth.models import (
    AdminSession,
    InvalidCredentialsError,
    hash_token,
    new_token,
)
from sales_app.admin_auth.passwords import hash_password, verify_password
from sales_app.admin_auth.service import (
    DEFAULT_LOGIN_WINDOW_SECONDS,
    DEFAULT_MAX_LOGIN_ATTEMPTS,
    _RateLimiter,
)
from sales_app.admin_auth.session_store import AdminSessionStore
from sales_app.client_profiles import ClientProfile, ClientProfileStore, normalize_login
from sales_core.ports import Clock


class ClientInactiveError(Exception):
    """The credentials are right but the client's account is inactive. Only raised after the
    password has been verified, so it never reveals whether a login exists."""


class WrongCurrentPasswordError(Exception):
    """The current password given to change the password was not the right one."""


@dataclass(frozen=True, slots=True)
class ClientLoginResult:
    profile: ClientProfile
    token: str  # returned once; never stored or logged
    session: AdminSession


class ClientAuthService:
    def __init__(
        self,
        profiles: ClientProfileStore,
        sessions: AdminSessionStore,
        clock: Clock,
        *,
        is_active: Callable[[str], bool],
        session_ttl: timedelta,
        max_login_attempts: int = DEFAULT_MAX_LOGIN_ATTEMPTS,
        login_window_seconds: float = DEFAULT_LOGIN_WINDOW_SECONDS,
    ) -> None:
        if session_ttl <= timedelta(0):
            raise ValueError("session_ttl must be positive")
        self._profiles = profiles
        self._sessions = sessions
        self._clock = clock
        self._is_active = is_active
        self._ttl = session_ttl
        self._limiter = _RateLimiter(
            max_attempts=max_login_attempts, window=timedelta(seconds=login_window_seconds)
        )

    def login(self, login: str, password: str) -> ClientLoginResult:
        """Raises `TooManyAttemptsError`, `InvalidCredentialsError` (unknown login or wrong
        password: indistinguishable) or `ClientInactiveError` (right password, inactive client)."""
        normalized = normalize_login(login)
        now = self._clock.now()
        self._limiter.check(normalized, now)
        profile = self._profiles.get_by_login(normalized)
        if profile is None or not verify_password(password, profile.password_hash):
            self._limiter.record_failure(normalized, now)
            raise InvalidCredentialsError("invalid login or password")
        self._limiter.record_success(normalized)
        if not self._is_active(profile.client_id):
            raise ClientInactiveError(profile.client_id)
        return self._open_session(profile)

    def logout(self, token: str) -> None:
        self._sessions.delete(hash_token(token))

    def authenticate(self, token: str) -> ClientProfile | None:
        """None if the token is missing, unknown or expired, or its client is gone or inactive
        (deactivating a client therefore ends its sessions at once)."""
        session = self._sessions.get(hash_token(token))
        if session is None:
            return None
        if session.is_expired(self._clock.now()):
            self._sessions.delete(session.token_hash)
            return None
        profile = self._profiles.get(session.admin_id)
        if profile is None or not self._is_active(profile.client_id):
            return None
        return profile

    def change_password(
        self, client_id: str, current_password: str, new_password: str
    ) -> ClientLoginResult:
        """Verify the current password, store only the new hash, end every session of this client
        and open a fresh one (the caller's browser stays logged in, every other one does not).
        Raises `WrongCurrentPasswordError` or `TooManyAttemptsError`."""
        profile = self._profiles.get(client_id)
        if profile is None:
            raise WrongCurrentPasswordError(client_id)
        key = f"password:{client_id}"
        now = self._clock.now()
        self._limiter.check(key, now)
        if not verify_password(current_password, profile.password_hash):
            self._limiter.record_failure(key, now)
            raise WrongCurrentPasswordError(client_id)
        self._limiter.record_success(key)
        updated = replace(
            profile, password_hash=hash_password(new_password), must_change_password=False
        )
        self._profiles.update(updated)
        self._sessions.delete_all_for(client_id)
        return self._open_session(updated)

    def _open_session(self, profile: ClientProfile) -> ClientLoginResult:
        now = self._clock.now()
        token = new_token()
        session = AdminSession(
            token_hash=hash_token(token),
            admin_id=profile.client_id,
            created_at=now,
            expires_at=now + self._ttl,
        )
        self._sessions.create(session)
        return ClientLoginResult(profile=profile, token=token, session=session)
