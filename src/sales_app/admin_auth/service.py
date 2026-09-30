"""Admin login, sessions, and the anti-brute-force guard.

The one place that turns a password into a session, and a session token back into an
`AdminUser`. Route protection (roles, client scope) is built on top of `authenticate` in
`sales_app.api.admin`; this service knows nothing about HTTP.
"""

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from sales_app.admin_auth.models import (
    AdminSession,
    AdminUser,
    InvalidCredentialsError,
    TooManyAttemptsError,
    hash_token,
    new_token,
)
from sales_app.admin_auth.passwords import verify_password
from sales_app.admin_auth.session_store import AdminSessionStore
from sales_app.admin_auth.user_store import AdminUserStore
from sales_core.ports import Clock

DEFAULT_MAX_LOGIN_ATTEMPTS = 5
DEFAULT_LOGIN_WINDOW_SECONDS = 900.0  # 15 minutes


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: AdminUser
    token: str  # returned once; never stored or logged
    session: AdminSession


class _RateLimiter:
    """Blunt, in-memory, per-process brute-force guard, keyed by normalised username.

    A sliding window: `max_attempts` failures within `window` blocks further attempts for that
    username until the oldest failure ages out. Resets on a successful login. In-memory is
    consistent with the rest of the deployment's single-process assumption; a restart clearing
    it is an acceptable MVP trade-off, not a security hole (it only ever makes brute force
    marginally easier for the few seconds a restart takes).
    """

    def __init__(self, *, max_attempts: int, window: timedelta) -> None:
        self._max_attempts = max_attempts
        self._window = window
        self._failures: dict[str, list[datetime]] = {}
        self._lock = threading.Lock()

    def check(self, username: str, now: datetime) -> None:
        with self._lock:
            attempts = [t for t in self._failures.get(username, ()) if now - t < self._window]
            self._failures[username] = attempts
            if len(attempts) >= self._max_attempts:
                raise TooManyAttemptsError(f"too many failed logins for {username!r}")

    def record_failure(self, username: str, now: datetime) -> None:
        with self._lock:
            self._failures.setdefault(username, []).append(now)

    def record_success(self, username: str) -> None:
        with self._lock:
            self._failures.pop(username, None)


class AdminAuthService:
    def __init__(
        self,
        users: AdminUserStore,
        sessions: AdminSessionStore,
        clock: Clock,
        *,
        session_ttl: timedelta,
        max_login_attempts: int = DEFAULT_MAX_LOGIN_ATTEMPTS,
        login_window_seconds: float = DEFAULT_LOGIN_WINDOW_SECONDS,
    ) -> None:
        if session_ttl <= timedelta(0):
            raise ValueError("session_ttl must be positive")
        self._users = users
        self._sessions = sessions
        self._clock = clock
        self._ttl = session_ttl
        self._limiter = _RateLimiter(
            max_attempts=max_login_attempts, window=timedelta(seconds=login_window_seconds)
        )

    def login(self, username: str, password: str) -> LoginResult:
        """Raises `TooManyAttemptsError` or `InvalidCredentialsError`. Never distinguishes
        "unknown username" from "wrong password", or "unknown" from "deactivated": all three
        answer the same way, so a caller learns nothing about which accounts exist."""
        normalized = username.strip().lower()
        now = self._clock.now()
        self._limiter.check(normalized, now)
        user = self._users.get_by_username(normalized)
        if user is None or not user.active or not verify_password(password, user.password_hash):
            self._limiter.record_failure(normalized, now)
            raise InvalidCredentialsError("invalid username or password")
        self._limiter.record_success(normalized)
        token = new_token()
        session = AdminSession(
            token_hash=hash_token(token),
            admin_id=user.id,
            created_at=now,
            expires_at=now + self._ttl,
        )
        self._sessions.create(session)
        return LoginResult(user=user, token=token, session=session)

    def logout(self, token: str) -> None:
        self._sessions.delete(hash_token(token))

    def authenticate(self, token: str) -> AdminUser | None:
        """None if the token is missing, unknown, expired, or its account is inactive or gone.
        An expired session is deleted as it is found: lazy cleanup, no cron needed."""
        session = self._sessions.get(hash_token(token))
        if session is None:
            return None
        if session.is_expired(self._clock.now()):
            self._sessions.delete(session.token_hash)
            return None
        user = self._users.get(session.admin_id)
        if user is None or not user.active:
            return None
        return user
