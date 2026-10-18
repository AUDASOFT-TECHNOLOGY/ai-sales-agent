"""A client's Gmail mailbox, connected with Google OAuth (XOAUTH2 over IMAP/SMTP).

Four pieces, all written against injected ports (nothing here knows the secret store, the routes or
the wiring):

- the signed state: HMAC-signed, short-lived, bound to one client and one pending session, and
  single-use (the pending session is consumed by the first callback, valid or not);
- `GoogleMailboxConnection`: start / callback / disconnect / status
  (`not_connected` | `connected` | `reauth_required`);
- `GoogleTokenSource`: a fresh access token on demand (in-memory cache, 60 s margin, one refresh at a
  time per client; `invalid_grant` marks the client `reauth_required`);
- `OAuthMailChannel` / `OAuthMailInbox`: wrappers of the mail adapter that fetch a token at each
  `send()` / poll and build XOAUTH2 settings, so a token that expired between two operations never
  matters.

Tokens are out of every `repr`, log and error message. The login proof is bound to the Gmail address
and a fingerprint of the refresh token, never to the volatile access token.
"""

import base64
import hashlib
import hmac
import json
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, Self

from sales_core.contracts import Channel, OutboundMessage, SendResult, SendStatus
from sales_core.ports import Clock
from sales_integrations.google_oauth import (
    MAIL_SCOPE,
    AccessGrant,
    GoogleAccessDeniedError,
    GoogleInsufficientScopeError,
    GoogleOAuthError,
    GoogleProtocolError,
    GoogleReauthRequiredError,
    GoogleTransientError,
    TokenGrant,
    code_challenge,
    new_code_verifier,
)
from sales_integrations.mail import (
    XOAUTH2,
    FetchedMail,
    ImapMailbox,
    ImapSettings,
    MailAuthError,
    MailChannel,
    MailCursorStore,
    MailUnavailableError,
    SmtpSettings,
    mailbox_digest,
    open_imap,
    open_smtp,
)
from sales_integrations.mail.mailbox import CONSUME

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_SECURITY = "starttls"
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
IMAP_SECURITY = "ssl"

NOT_CONNECTED = "not_connected"
CONNECTED = "connected"
REAUTH_REQUIRED = "reauth_required"

STATE_TTL_SECONDS = 600
TOKEN_MARGIN_SECONDS = 60
MIN_SIGNING_KEY_BYTES = 16


class InvalidGoogleStateError(Exception):
    """The `state` of a callback is forged, altered, expired, already used, or not this client's.

    One fixed message for every cause: nothing tells an attacker which check failed."""

    def __init__(self) -> None:
        super().__init__("the Google connection request is not valid")


class AddressAlreadyConnectedError(GoogleOAuthError):
    """This Gmail address is already connected to another client: a mailbox belongs to one client
    only (two clients reading the same one would take each other's replies)."""

    def __init__(self) -> None:
        super().__init__("this Gmail address is already connected to another account")


# ---- ports ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, repr=False)
class GoogleGrant:
    """What is kept for one client's connected mailbox (the refresh token is a secret)."""

    refresh_token: str = field(repr=False)
    address: str
    reauth_required: bool = False

    def __repr__(self) -> str:
        return "GoogleGrant()"


class GoogleGrantStore(Protocol):
    """Where the grant of each client lives (a secret store behind it; never a database column)."""

    def load(self, client_id: str) -> GoogleGrant | None: ...

    def save(self, client_id: str, refresh_token: str, address: str) -> None:
        """Keep a new grant; it also clears any `reauth_required` mark. Raises
        `AddressAlreadyConnectedError` when another client holds this address."""
        ...

    def mark_reauth_required(self, client_id: str) -> None: ...

    def delete(self, client_id: str) -> None: ...


@dataclass(frozen=True, slots=True, repr=False)
class PendingAuthorization:
    client_id: str
    code_verifier: str = field(repr=False)
    expires_at: datetime

    def __repr__(self) -> str:
        return "PendingAuthorization()"


class PendingAuthorizationStore(Protocol):
    """Short-lived, single-use memory of consents under way (the PKCE verifier lives here)."""

    def put(self, session_id: str, pending: PendingAuthorization) -> None: ...

    def take(self, session_id: str) -> PendingAuthorization | None:
        """Return and forget, atomically: a second `take` of the same id returns `None`."""
        ...


class GoogleOAuthPort(Protocol):
    """The part of `google_oauth.GoogleOAuthClient` this module uses."""

    def authorization_url(
        self, *, state: str, code_challenge: str, login_hint: str | None = None
    ) -> str: ...

    def exchange_code(self, *, code: str, code_verifier: str) -> TokenGrant: ...

    def refresh(self, refresh_token: str) -> AccessGrant: ...

    def revoke(self, token: str) -> None: ...

    def account_address(self, access_token: str) -> str: ...


class InMemoryGoogleGrantStore:
    def __init__(self) -> None:
        self._grants: dict[str, GoogleGrant] = {}
        self._lock = threading.Lock()

    def load(self, client_id: str) -> GoogleGrant | None:
        with self._lock:
            return self._grants.get(client_id)

    def save(self, client_id: str, refresh_token: str, address: str) -> None:
        with self._lock:
            self._grants[client_id] = GoogleGrant(refresh_token, address)

    def mark_reauth_required(self, client_id: str) -> None:
        with self._lock:
            held = self._grants.get(client_id)
            if held is not None:
                self._grants[client_id] = GoogleGrant(held.refresh_token, held.address, True)

    def delete(self, client_id: str) -> None:
        with self._lock:
            self._grants.pop(client_id, None)

    def __repr__(self) -> str:
        return "InMemoryGoogleGrantStore()"


class InMemoryPendingAuthorizationStore:
    def __init__(self) -> None:
        self._pending: dict[str, PendingAuthorization] = {}
        self._lock = threading.Lock()

    def put(self, session_id: str, pending: PendingAuthorization) -> None:
        with self._lock:
            self._pending[session_id] = pending

    def take(self, session_id: str) -> PendingAuthorization | None:
        with self._lock:
            return self._pending.pop(session_id, None)

    def __repr__(self) -> str:
        return "InMemoryPendingAuthorizationStore()"


# ---- the signed state -----------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_state(key: bytes, client_id: str, session_id: str, expires_at: datetime) -> str:
    payload = json.dumps(
        {"c": client_id, "s": session_id, "e": int(expires_at.timestamp())},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    body = _b64(payload)
    return f"{body}.{_b64(hmac.new(key, body.encode('ascii'), hashlib.sha256).digest())}"


def verify_state(key: bytes, state: str, now: datetime) -> tuple[str, str]:
    """(client_id, session_id) of a state that is authentic and not expired; else raise."""
    try:
        body, signature = state.split(".")
        expected = hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise InvalidGoogleStateError
        payload = json.loads(_unb64(body))
        client_id, session_id, expires = payload["c"], payload["s"], payload["e"]
        if not (isinstance(client_id, str) and isinstance(session_id, str)):
            raise InvalidGoogleStateError
        if isinstance(expires, bool) or not isinstance(expires, int):
            raise InvalidGoogleStateError
    except InvalidGoogleStateError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError):  # incl. bad base64 / JSON
        raise InvalidGoogleStateError from None
    if now.timestamp() >= expires:
        raise InvalidGoogleStateError
    return client_id, session_id


def scopes_cover(granted: str, required: tuple[str, ...]) -> bool:
    """Whether the space-separated `granted` scopes include every required one."""
    held = set(granted.split())
    return all(scope in held for scope in required)


# ---- fingerprints and proof -----------------------------------------------------------------


def refresh_token_fingerprint(refresh_token: str) -> str:
    """A stable, non-reversible mark of a refresh token (it changes when the person reconnects)."""
    return hashlib.sha256(b"google-refresh|" + refresh_token.encode("utf-8")).hexdigest()[:32]


def mailbox_login_proof(key: bytes, address: str, refresh_token: str) -> str:
    """Proof that this Gmail address was connected with this grant. The same for any access token,
    different after a reconnection or for another address."""
    message = f"google-mailbox|{address.strip().lower()}|{refresh_token_fingerprint(refresh_token)}"
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


# ---- the connection service -----------------------------------------------------------------


@dataclass(frozen=True, slots=True, repr=False)
class ConnectionStatus:
    state: str  # not_connected | connected | reauth_required
    address: str | None = None

    def __repr__(self) -> str:
        return f"ConnectionStatus(state={self.state!r})"


class GoogleMailboxConnection:
    def __init__(
        self,
        *,
        oauth: GoogleOAuthPort,
        grants: GoogleGrantStore,
        pending: PendingAuthorizationStore,
        signing_key: bytes,
        clock: Clock,
        state_ttl_seconds: int = STATE_TTL_SECONDS,
        required_scopes: tuple[str, ...] = (MAIL_SCOPE,),
    ) -> None:
        if len(signing_key) < MIN_SIGNING_KEY_BYTES:
            raise ValueError("the state signing key is too short")
        self._required_scopes = required_scopes
        self._oauth = oauth
        self._grants = grants
        self._pending = pending
        self._key = signing_key
        self._clock = clock
        self._ttl = timedelta(seconds=state_ttl_seconds)

    def __repr__(self) -> str:
        return "GoogleMailboxConnection()"

    def start(self, client_id: str, *, login_hint: str | None = None) -> str:
        """The Google consent URL for this client; its `state` is good once, for a few minutes."""
        session_id = secrets.token_urlsafe(24)
        verifier = new_code_verifier()
        expires_at = self._clock.now() + self._ttl
        self._pending.put(session_id, PendingAuthorization(client_id, verifier, expires_at))
        state = sign_state(self._key, client_id, session_id, expires_at)
        return self._oauth.authorization_url(
            state=state, code_challenge=code_challenge(verifier), login_hint=login_hint
        )

    def callback(
        self,
        *,
        state: str,
        client_id: str,
        code: str | None = None,
        error: str | None = None,
    ) -> ConnectionStatus:
        """Finish a consent. `client_id` is the authenticated client of the request: a state made
        for another client is refused. Raises `InvalidGoogleStateError`, `GoogleAccessDeniedError`,
        or another `GoogleOAuthError`."""
        now = self._clock.now()
        state_client, session_id = verify_state(self._key, state or "", now)
        pending = self._pending.take(session_id)  # consumed whatever happens next
        if (
            pending is None
            or pending.client_id != state_client
            or state_client != client_id
            or now >= pending.expires_at
        ):
            raise InvalidGoogleStateError
        if error:
            raise GoogleAccessDeniedError("Google access was refused")
        if not code:
            raise GoogleProtocolError("Google's answer was not understood")
        grant = self._oauth.exchange_code(code=code, code_verifier=pending.code_verifier)
        if not grant.refresh_token:
            # Without a refresh token nothing could be kept; drop the access grant Google made.
            self._revoke_quietly(grant.access_token)
            raise GoogleProtocolError("Google's token answer was not understood")
        # Granular consent: the person may have granted less than asked. Fail closed: only what
        # Google says was granted counts, and an unknown scope (empty) counts as nothing.
        if not scopes_cover(grant.scope, self._required_scopes):
            self._revoke_quietly(grant.refresh_token)
            raise GoogleInsufficientScopeError("Google did not grant the mailbox permission")
        try:
            address = self._oauth.account_address(grant.access_token)
        except Exception:
            self._revoke_quietly(grant.refresh_token)  # no orphan grant at Google's side
            raise
        previous = self._grants.load(client_id)
        try:
            self._grants.save(client_id, grant.refresh_token, address)
        except Exception:
            self._revoke_quietly(grant.refresh_token)  # nothing kept here: nothing left at Google
            raise
        if previous is not None and previous.refresh_token != grant.refresh_token:
            self._revoke_quietly(previous.refresh_token)  # reconnecting replaces, never piles up
        return ConnectionStatus(CONNECTED, address)

    def _revoke_quietly(self, token: str) -> None:
        try:
            self._oauth.revoke(token)
        except (GoogleTransientError, GoogleProtocolError, GoogleAccessDeniedError):
            pass

    def disconnect(self, client_id: str) -> bool:
        """Forget the grant, and ask Google to revoke it. Returns whether Google confirmed; the
        grant is gone locally either way (a revocation Google could not take now is not retried)."""
        held = self._grants.load(client_id)
        if held is None:
            return True
        self._grants.delete(client_id)
        try:
            self._oauth.revoke(held.refresh_token)
        except (GoogleTransientError, GoogleProtocolError, GoogleAccessDeniedError):
            return False
        return True

    def status(self, client_id: str) -> ConnectionStatus:
        held = self._grants.load(client_id)
        if held is None:
            return ConnectionStatus(NOT_CONNECTED)
        return ConnectionStatus(
            REAUTH_REQUIRED if held.reauth_required else CONNECTED, held.address
        )

    def proof(self, client_id: str) -> str | None:
        held = self._grants.load(client_id)
        if held is None or held.reauth_required:
            return None
        return mailbox_login_proof(self._key, held.address, held.refresh_token)


# ---- the token source -----------------------------------------------------------------------


class GoogleTokenSource:
    """Fresh access tokens for one client. Thread-safe: concurrent callers share one refresh."""

    def __init__(
        self,
        *,
        client_id: str,
        oauth: GoogleOAuthPort,
        grants: GoogleGrantStore,
        clock: Clock,
        margin_seconds: int = TOKEN_MARGIN_SECONDS,
        required_scopes: tuple[str, ...] = (MAIL_SCOPE,),
    ) -> None:
        self._required_scopes = required_scopes
        self._client_id = client_id
        self._oauth = oauth
        self._grants = grants
        self._clock = clock
        self._margin = timedelta(seconds=margin_seconds)
        self._lock = threading.Lock()
        self._token = ""
        self._expires_at: datetime | None = None
        self._grant_mark = ""

    def __repr__(self) -> str:
        return "GoogleTokenSource()"

    def access_token(self) -> str:
        """Raises `GoogleReauthRequiredError` (not connected, revoked or `invalid_grant`) or
        `GoogleTransientError` (try again later; the grant is untouched)."""
        with self._lock:
            held = self._grants.load(self._client_id)
            if held is None or held.reauth_required:
                self._forget()
                raise GoogleReauthRequiredError("the Google authorisation must be renewed")
            mark = refresh_token_fingerprint(held.refresh_token)
            if mark != self._grant_mark:
                self._forget()  # reconnected, or another grant: the cache is not this one's
            if self._token and self._expires_at and self._clock.now() < self._expires_at:
                return self._token
            try:
                fresh = self._oauth.refresh(held.refresh_token)
            except GoogleReauthRequiredError:
                self._forget()
                self._grants.mark_reauth_required(self._client_id)
                raise
            if fresh.scope and not scopes_cover(fresh.scope, self._required_scopes):
                # The permission was narrowed at Google's side: sending would fail; ask again.
                self._forget()
                self._grants.mark_reauth_required(self._client_id)
                raise GoogleReauthRequiredError("the Google authorisation must be renewed")
            self._token = fresh.access_token
            self._expires_at = (
                self._clock.now() + timedelta(seconds=fresh.expires_in) - self._margin
            )
            self._grant_mark = mark
            return self._token

    def _forget(self) -> None:
        self._token = ""
        self._expires_at = None
        self._grant_mark = ""


class AccessTokenSource(Protocol):
    def access_token(self) -> str: ...


# ---- the mandatory send cap -------------------------------------------------------------------

CAP_NOT_CONFIGURED = "send_cap_not_configured"
CAP_REACHED = "send_cap_reached"


class SendCounter(Protocol):
    """Atomic per-day counter (a shared store when more than one process sends)."""

    def try_increment(self, day: str, limit: int) -> bool: ...

    def decrement(self, day: str) -> None: ...


class InMemorySendCounter:
    """Single-process counter: a restart forgets the day's count (use a shared store in production)."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def try_increment(self, day: str, limit: int) -> bool:
        with self._lock:
            used = self._counts.get(day, 0)
            if used >= limit:
                return False
            self._counts[day] = used + 1
            return True

    def decrement(self, day: str) -> None:
        with self._lock:
            if self._counts.get(day, 0) > 0:
                self._counts[day] -= 1

    def __repr__(self) -> str:
        return "InMemorySendCounter()"


class SendGuard(Protocol):
    def reserve(self) -> str | None:
        """None: one send is reserved. Else the reason code it is refused (nothing reserved)."""
        ...

    def release(self) -> None:
        """Give back the reservation of a send that did not leave."""
        ...


class CapEnforcedByWiring:
    """The guard to give an `OAuthMailChannel` that the application's wiring already puts behind the
    shared daily cap (`sales_app.send_cap.SendCapChannel`, applied to every e-mail channel when the
    runtime builds it): it reserves nothing, so a message is counted once, in the shared SQL counter,
    not a second time here. Never use it on a channel that is not wrapped by that wiring."""

    def __repr__(self) -> str:
        return "CapEnforcedByWiring()"

    def reserve(self) -> str | None:
        return None

    def release(self) -> None:
        return None


class DailySendCap:
    """A per-client daily cap that must be configured. `limit` is read at each send (the Admin may
    change it); `None` or below 1 means *not configured* and refuses every send: there is no default
    and no implicit unlimited. The day is the UTC calendar day; Gmail's own limits apply on top."""

    def __init__(
        self, limit: Callable[[], int | None], clock: Clock, counter: SendCounter | None = None
    ) -> None:
        self._limit = limit
        self._clock = clock
        self._counter = counter or InMemorySendCounter()
        self._local = threading.local()

    def __repr__(self) -> str:
        return "DailySendCap()"

    def reserve(self) -> str | None:
        limit = self._limit()
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return CAP_NOT_CONFIGURED
        day = self._clock.now().astimezone(UTC).date().isoformat()
        if not self._counter.try_increment(day, limit):
            return CAP_REACHED
        self._local.day = day
        return None

    def release(self) -> None:
        day = getattr(self._local, "day", None)
        if day is not None:
            self._counter.decrement(day)
            self._local.day = None


# ---- the XOAUTH2 wrappers -------------------------------------------------------------------


class OAuthMailChannel:
    """`MailChannel` with a fresh Gmail access token for each `send()`."""

    def __init__(
        self,
        tokens: AccessTokenSource,
        address: str,
        *,
        from_name: str | None = None,
        subject: str | None = None,
        clock: Clock | None = None,
        smtp_factory: Callable[[SmtpSettings], Any] = open_smtp,
        send_guard: SendGuard | None = None,
    ) -> None:
        self._guard = send_guard
        self._tokens = tokens
        self._address = address
        self._from_name = from_name
        self._subject = subject
        self._clock = clock
        self._factory = smtp_factory

    def __repr__(self) -> str:
        return "OAuthMailChannel()"

    @property
    def channel(self) -> Channel:
        return Channel.EMAIL

    def send(self, message: OutboundMessage) -> SendResult:
        # Fail closed: no guard means no configured cap, so nothing leaves.
        refusal = self._guard.reserve() if self._guard is not None else CAP_NOT_CONFIGURED
        if refusal is not None:
            return self._failure(refusal, "the daily send cap is not configured or is reached")
        # If this raises, the message may have left (the outcome is unknown): the place stays used.
        # Over-counting is safe, exceeding the cap is not. Only a certain failure gives it back.
        result = self._send_reserved(message)
        if not result.ok:
            self._release()
        return result

    def _release(self) -> None:
        if self._guard is not None:
            self._guard.release()

    def _send_reserved(self, message: OutboundMessage) -> SendResult:
        try:
            token = self._tokens.access_token()
        except GoogleReauthRequiredError:
            return self._failure("reauth_required", "mailbox authorisation must be renewed")
        except GoogleTransientError:
            return self._failure("token_unavailable", "authorisation temporarily unavailable")
        extra: dict[str, Any] = {"subject": self._subject} if self._subject else {}
        settings = SmtpSettings(
            host=SMTP_HOST,
            port=SMTP_PORT,
            security=SMTP_SECURITY,
            username=self._address,
            password="",
            from_address=self._address,
            from_name=self._from_name,
            auth=XOAUTH2,
            access_token=token,
            **extra,
        )
        return MailChannel(settings, factory=self._factory, clock=self._clock).send(message)

    def _failure(self, code: str, text: str) -> SendResult:
        now = self._clock.now() if self._clock is not None else datetime.now(UTC)
        return SendResult(
            status=SendStatus.FAILED_TRANSIENT, timestamp=now, error_code=code, error_message=text
        )


class OAuthMailInbox:
    """`ImapMailbox` shape, with a fresh Gmail access token each time a poll opens its connection.

    One poll, one connection, as the adapter: `close()` ends it, and the next `fetch()` opens a new
    one with a new token."""

    def __init__(
        self,
        tokens: AccessTokenSource,
        address: str,
        *,
        folder: str = "INBOX",
        read_mode: str = CONSUME,
        clock: Clock | None = None,
        imap_factory: Callable[[ImapSettings], Any] = open_imap,
    ) -> None:
        self._tokens = tokens
        self._address = address
        self._folder = folder
        self._read_mode = read_mode
        self._clock = clock
        self._factory = imap_factory
        self._cursors: MailCursorStore | None = None
        self._inner: ImapMailbox | None = None
        self.revalidated = False

    def __repr__(self) -> str:
        return "OAuthMailInbox()"

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def needs_cursor(self) -> bool:
        return self._read_mode != CONSUME

    @property
    def filters_by_sender(self) -> bool:
        return self._read_mode != CONSUME

    @property
    def cursor_key(self) -> str:
        return mailbox_digest(IMAP_HOST, self._address, self._folder)

    def use_cursor(self, store: MailCursorStore | None) -> None:
        self._cursors = store
        if self._inner is not None:
            self._inner.use_cursor(store)

    def fetch(self, limit: int = 50, accept: Any = None) -> list[FetchedMail]:
        inner = self._open()
        try:
            return inner.fetch(limit, accept)
        finally:
            self.revalidated = inner.revalidated

    def acknowledge(self, handle: str) -> None:
        self._open().acknowledge(handle)

    def close(self) -> None:
        inner, self._inner = self._inner, None
        if inner is not None:
            inner.close()

    def _open(self) -> ImapMailbox:
        if self._inner is not None:
            return self._inner
        try:
            token = self._tokens.access_token()
        except GoogleReauthRequiredError:
            raise MailAuthError("the mailbox refused the login") from None
        except GoogleTransientError:
            raise MailUnavailableError("the mail server could not be reached") from None
        settings = ImapSettings(
            host=IMAP_HOST,
            port=IMAP_PORT,
            security=IMAP_SECURITY,
            username=self._address,
            password="",
            mailbox=self._folder,
            own_addresses=(self._address,),
            read_mode=self._read_mode,
            auth=XOAUTH2,
            access_token=token,
        )
        inner = ImapMailbox(settings, factory=self._factory, clock=self._clock)
        inner.use_cursor(self._cursors)
        self._inner = inner
        return inner
