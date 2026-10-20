"""A client's own Telegram account, wired to the application: the sign-in, the stored session, the
connection state, and the channel and inbox that use it.

The client signs in with Telegram itself (its phone number, the code Telegram sends to its app and,
if it has one, its two-step password). **Revansa keeps the session only**: a full access key to the
account, encrypted in the client's scope of the secret store. The code and the password are passed
to Telegram and forgotten; between two steps of a sign-in only the temporary session and the code's
hash are held, in this process's memory, for a few minutes. Nothing here is ever returned by an API,
logged or put in an error.

Everything is resolved when it is used, so what the Admin saves (the platform's Telegram application)
applies without a restart, and a session that Telegram ended is seen at the next use:

- the platform's application (`telegram.api_id` + `telegram.api_hash`, global, Admin only);
- the client's session, account id (UNIQUE across clients: one account serves one client) and the two
  marks (`reauth`: the session ended, `limited`: Telegram limited the account for unsolicited
  messages), written only by this module, never through the Admin credentials API;
- the channel and the inbox of a client in `account` mode, built from them.

Limit: a sign-in under way lives in this process's memory (Revansa runs ONE worker, like the Gmail
consent): after a restart the client simply starts again. A session, by contrast, survives restarts.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sales_app.config.secrets import (
    TELEGRAM_API_HASH,
    TELEGRAM_API_ID,
    TELEGRAM_USER_ACCOUNT,
    TELEGRAM_USER_LIMITED,
    TELEGRAM_USER_REAUTH,
    TELEGRAM_USER_SESSION,
)
from sales_app.secret_store import GLOBAL_SCOPE, SecretStore, SecretStoreError, client_scope
from sales_app.secret_store.errors import DuplicateSecretError
from sales_core.ports import Clock
from sales_integrations.telegram.account import (
    AUTH_LOST,
    AccountClient,
    AccountFailure,
    LoginTicket,
    PasswordNeeded,
    SignedIn,
    TelegramAccountChannel,
    TelegramAccountInbox,
)
from sales_integrations.telegram.telethon_client import TelethonAccount, telethon_available

from sales_app.notifications import tell as _tell

log = logging.getLogger(__name__)

_ACTOR = "system:telegram-account"
LOGIN_TTL = timedelta(minutes=10)  # a code Telegram sent is useless after that
MAX_STEPS = 5  # wrong codes / passwords tolerated in one sign-in
MAX_STARTS = 5  # sign-ins a client may start per hour: Telegram punishes repeated code requests
LINKS_PER_POLL = 5  # prospects whose Telegram account is looked up per inbox poll

NOT_CONNECTED = "not_connected"
CONNECTED = "connected"
REAUTH_REQUIRED = "reauth_required"
UNAVAILABLE = "unavailable"

AccountFactory = Callable[[int, str, str | None], AccountClient]  # (api id, api hash, session)


def telethon_factory(api_id: int, api_hash: str, session: str | None) -> AccountClient:
    return TelethonAccount(api_id, api_hash, session)


class AccountLoginError(Exception):
    """A sign-in step that did not work, by `kind` (a fixed word): `not_configured`, `bad_phone`,
    `bad_code`, `code_expired`, `bad_password`, `too_many`, `flood`, `unreachable`, `no_login`,
    `already_used`, `unavailable`. Never carries a number, a code, a password or a session."""

    def __init__(self, kind: str, *, retry_after: int | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.retry_after = retry_after


class AccountNotReadyError(RuntimeError):
    """The client's Telegram account cannot be used now (not connected, or to be connected again)."""


@dataclass(frozen=True, slots=True)
class AccountStatus:
    state: str  # NOT_CONNECTED | CONNECTED | REAUTH_REQUIRED | UNAVAILABLE
    limited: bool = False  # Telegram limited the account for unsolicited messages
    login: str | None = None  # a sign-in under way is waiting for `code` or `password`


@dataclass(slots=True, repr=False)
class _Pending:
    phone: str
    ticket: LoginTicket
    expires: datetime
    stage: str = "code"
    steps: int = 0

    def __repr__(self) -> str:
        return "_Pending()"


def normalise_phone(raw: str) -> str | None:
    """`+` and 8 to 15 digits (E.164), from what a person types; None if it cannot be one."""
    digits = "".join(c for c in raw if c.isdigit())
    if not 8 <= len(digits) <= 15 or any(c not in "0123456789 +-(). " for c in raw):
        return None
    return f"+{digits}"


class TelegramAccountService:
    """Everything the application needs for Telegram from a client's own account."""

    def __init__(
        self,
        *,
        secrets: SecretStore,
        clock: Clock,
        factory: AccountFactory = telethon_factory,
        available: Callable[[], bool] = telethon_available,
    ) -> None:
        self._secrets = secrets
        self._clock = clock
        self._factory = factory
        self._available = available
        self._pending: dict[str, _Pending] = {}
        self._starts: dict[str, list[datetime]] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "TelegramAccountService()"

    # ---- the platform's application -----------------------------------------------------------

    def _global(self, key: str) -> str | None:
        try:
            return self._secrets.get(GLOBAL_SCOPE, key)
        except SecretStoreError:
            return None

    def _application(self) -> tuple[int, str] | None:
        api_id, api_hash = self._global(TELEGRAM_API_ID), self._global(TELEGRAM_API_HASH)
        if not api_id or not api_hash or not api_id.isdigit():
            return None
        return int(api_id), api_hash

    def configured(self) -> bool:
        return self._application() is not None and self._available()

    def missing(self) -> list[str]:
        """What the Admin still has to set for this mode to be offered. Names only."""
        found = []
        if not self._global(TELEGRAM_API_ID):
            found.append("Telegram application id (api_id)")
        if not self._global(TELEGRAM_API_HASH):
            found.append("Telegram application hash (api_hash)")
        if not self._available():
            found.append("the Telegram library on the server (telethon)")
        return found

    def _client(self, session: str | None) -> AccountClient:
        application = self._application()
        if application is None or not self._available():
            raise AccountLoginError("not_configured")
        return self._factory(application[0], application[1], session)

    # ---- what is stored -----------------------------------------------------------------------

    def _get(self, client_id: str, key: str) -> str | None:
        return self._secrets.get(client_scope(client_id), key)

    def status(self, client_id: str) -> AccountStatus:
        try:
            session = self._get(client_id, TELEGRAM_USER_SESSION)
            reauth = self._get(client_id, TELEGRAM_USER_REAUTH) == "1"
            limited = self._get(client_id, TELEGRAM_USER_LIMITED) == "1"
        except SecretStoreError:
            return AccountStatus(UNAVAILABLE)
        with self._lock:
            pending = self._pending.get(client_id)
            login = pending.stage if pending and pending.expires > self._clock.now() else None
        if not session:
            return AccountStatus(NOT_CONNECTED, login=login)
        return AccountStatus(REAUTH_REQUIRED if reauth else CONNECTED, limited=limited, login=login)

    def account_id(self, client_id: str) -> str | None:
        try:
            return self._get(client_id, TELEGRAM_USER_ACCOUNT)
        except SecretStoreError:
            return None

    def _mark(self, client_id: str, key: str) -> None:
        try:
            self._secrets.set(client_scope(client_id), key, "1", by=_ACTOR)
        except SecretStoreError:
            log.warning("could not record the state of a Telegram account")

    on_reauth: Callable[[str], None] | None = None  # set by the composition root (notifications)

    def mark_reauth_required(self, client_id: str) -> None:
        self._mark(client_id, TELEGRAM_USER_REAUTH)
        _tell(self.on_reauth, client_id)

    def mark_limited(self, client_id: str) -> None:
        self._mark(client_id, TELEGRAM_USER_LIMITED)

    def clear_limited(self, client_id: str) -> None:
        """The Admin's decision, once Telegram lifted the limit (checked with @SpamBot)."""
        try:
            self._secrets.delete(client_scope(client_id), TELEGRAM_USER_LIMITED)
        except SecretStoreError:
            raise AccountLoginError("unavailable") from None

    def _save(self, client_id: str, signed: SignedIn) -> None:
        scope = client_scope(client_id)
        try:
            previous = self._secrets.get(scope, TELEGRAM_USER_ACCOUNT)
            # The account first, and unique: another client holding it refuses the whole save.
            self._secrets.set(
                scope, TELEGRAM_USER_ACCOUNT, signed.identity.user_id, by=_ACTOR, unique=True
            )
            try:
                self._secrets.set(scope, TELEGRAM_USER_SESSION, signed.session, by=_ACTOR)
            except SecretStoreError:
                if previous:
                    self._secrets.set(
                        scope, TELEGRAM_USER_ACCOUNT, previous, by=_ACTOR, unique=True
                    )
                else:
                    self._secrets.delete(scope, TELEGRAM_USER_ACCOUNT)
                raise
            self._secrets.delete(scope, TELEGRAM_USER_REAUTH)
            if previous != signed.identity.user_id:
                self._secrets.delete(scope, TELEGRAM_USER_LIMITED)  # another account, another state
        except DuplicateSecretError:
            raise AccountLoginError("already_used") from None
        except SecretStoreError:
            raise AccountLoginError("unavailable") from None

    # ---- signing in ---------------------------------------------------------------------------

    def start(self, client_id: str, phone: str) -> None:
        """Ask Telegram to send the sign-in code to the person's own app."""
        number = normalise_phone(phone)
        if number is None:
            raise AccountLoginError("bad_phone")
        now = self._clock.now()
        with self._lock:
            recent = [t for t in self._starts.get(client_id, []) if now - t < timedelta(hours=1)]
            if len(recent) >= MAX_STARTS:
                raise AccountLoginError("too_many")
            self._starts[client_id] = [*recent, now]
        try:
            ticket = self._client(None).request_code(number)
        except AccountFailure as failure:
            raise _login_error(failure) from None
        with self._lock:
            self._pending[client_id] = _Pending(number, ticket, now + LOGIN_TTL)

    def _take(self, client_id: str, stage: str) -> _Pending:
        with self._lock:
            pending = self._pending.get(client_id)
            if pending is None or pending.stage != stage or pending.expires <= self._clock.now():
                self._pending.pop(client_id, None)
                raise AccountLoginError("no_login")
            pending.steps += 1
            if pending.steps > MAX_STEPS:
                self._pending.pop(client_id, None)
                raise AccountLoginError("too_many")
            return pending

    def confirm_code(self, client_id: str, code: str) -> AccountStatus:
        pending = self._take(client_id, "code")
        digits = "".join(c for c in code if c.isdigit())
        if not 4 <= len(digits) <= 8:
            raise AccountLoginError("bad_code")
        try:
            signed = self._client(None).sign_in_code(pending.ticket, pending.phone, digits)
        except PasswordNeeded as needed:
            with self._lock:
                pending.ticket, pending.stage, pending.steps = needed.ticket, "password", 0
            return self.status(client_id)
        except AccountFailure as failure:
            if failure.kind == "code_expired":
                with self._lock:
                    self._pending.pop(client_id, None)
            raise _login_error(failure) from None
        return self._finish(client_id, signed)

    def confirm_password(self, client_id: str, password: str) -> AccountStatus:
        pending = self._take(client_id, "password")
        if not password:
            raise AccountLoginError("bad_password")
        try:
            signed = self._client(None).sign_in_password(pending.ticket, password)
        except AccountFailure as failure:
            raise _login_error(failure) from None
        return self._finish(client_id, signed)

    def _finish(self, client_id: str, signed: SignedIn) -> AccountStatus:
        try:
            self._save(client_id, signed)
        except AccountLoginError as error:
            if error.kind == "already_used":
                # The session just made would stay valid at Telegram: end it, nothing is kept.
                try:
                    self._factory(*self._app_or_fail(), signed.session).log_out()
                except (AccountFailure, AccountLoginError):
                    log.warning("could not end a Telegram session that was refused")
            raise
        finally:
            with self._lock:
                self._pending.pop(client_id, None)
        return self.status(client_id)

    def _app_or_fail(self) -> tuple[int, str]:
        application = self._application()
        if application is None:
            raise AccountLoginError("not_configured")
        return application

    def cancel(self, client_id: str) -> None:
        with self._lock:
            self._pending.pop(client_id, None)

    # ---- disconnecting ------------------------------------------------------------------------

    def disconnect(self, client_id: str) -> bool:
        """Forget the session here whatever happens; end it at Telegram when that can be done.
        True if Telegram confirmed (or there was nothing to end)."""
        self.cancel(client_id)
        scope = client_scope(client_id)
        try:
            session = self._secrets.get(scope, TELEGRAM_USER_SESSION)
            for key in (
                TELEGRAM_USER_SESSION,
                TELEGRAM_USER_ACCOUNT,
                TELEGRAM_USER_REAUTH,
                TELEGRAM_USER_LIMITED,
            ):
                self._secrets.delete(scope, key)
        except SecretStoreError:
            raise AccountLoginError("unavailable") from None
        if not session:
            return True
        application = self._application()
        if application is None or not self._available():
            return False
        try:
            self._factory(application[0], application[1], session).log_out()
        except AccountFailure as failure:
            return failure.kind == AUTH_LOST  # already ended at Telegram: nothing left to end
        return True

    # ---- the runtime --------------------------------------------------------------------------

    def _session(self, client_id: str) -> tuple[str, str]:
        try:
            session = self._get(client_id, TELEGRAM_USER_SESSION)
            reauth = self._get(client_id, TELEGRAM_USER_REAUTH) == "1"
            account = self._get(client_id, TELEGRAM_USER_ACCOUNT)
        except SecretStoreError:
            raise AccountNotReadyError("the Telegram connection cannot be read") from None
        if not session or not account:
            raise AccountNotReadyError("no Telegram account is connected to this account")
        if reauth:
            raise AccountNotReadyError("the Telegram connection must be renewed")
        return session, account

    def _account_client(self, client_id: str) -> tuple[AccountClient, str]:
        session, account = self._session(client_id)
        application = self._application()
        if application is None or not self._available():
            raise AccountNotReadyError("Telegram accounts are not configured on this platform")
        return self._factory(application[0], application[1], session), account

    def channel(self, config: Any) -> TelegramAccountChannel:
        client_id = config.client_id
        client, _ = self._account_client(client_id)
        return TelegramAccountChannel(
            client,
            clock=self._clock,
            limited=lambda: self.status(client_id).limited,
            on_limited=lambda: self.mark_limited(client_id),
            on_auth_lost=lambda: self.mark_reauth_required(client_id),
        )

    def inbox(self, config: Any) -> TelegramAccountInbox:
        client_id = config.client_id
        client, account = self._account_client(client_id)
        return TelegramAccountInbox(
            client,
            account,
            clock=self._clock,
            link_limit=LINKS_PER_POLL,
            on_auth_lost=lambda: self.mark_reauth_required(client_id),
        )


def _login_error(failure: AccountFailure) -> AccountLoginError:
    kind = {
        "bad_phone": "bad_phone",
        "bad_code": "bad_code",
        "code_expired": "code_expired",
        "bad_password": "bad_password",
        "login_flood": "flood",
        "flood_wait": "flood",
        "unreachable": "unreachable",
    }.get(failure.kind, "unavailable")
    return AccountLoginError(kind, retry_after=failure.retry_after)
