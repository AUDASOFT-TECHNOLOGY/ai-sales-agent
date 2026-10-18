"""The Gmail mailbox mode, wired to the application: platform credentials, persistent grants, the
connection service and the runtime channel/inbox.

Everything here is resolved when it is used, never frozen at start-up, so what the Admin saves in
Settings applies without a restart (like the payment provider):

- `read_platform`: the Google application (client id, client secret, state key, public base URL)
  from the secret store. All four, or Gmail is "not configured" and the client waits.
- `SecretStoreGrantStore`: a client's grant (refresh token, address, `reauth_required`) in the
  encrypted secret store, client scope. The keys are not in the credential catalog, so the Admin
  credentials API can neither write nor list them; only the OAuth callback writes them. The address
  is stored UNIQUE across clients. Nothing is ever returned by an API.
- `GoogleConnectionService`: `GoogleMailboxConnection` rebuilt from the current credentials at each
  call, over the persistent grants and ONE pending-authorisation store.
- `GmailMailService`: the `OAuthMailChannel` / `OAuthMailInbox` of a client in Gmail mode. The
  channel uses `CapEnforcedByWiring` because `wiring.runtime._metered` puts every e-mail channel
  behind the shared daily send cap; the inbox reads in `observe` mode (a person's mailbox: nothing
  marked read, position kept in the database, only prospects' replies read).

Limit: the pending authorisations (the PKCE verifier of a consent under way) live in this process's
memory. Revansa runs ONE worker by design (uvicorn without `--workers`); a consent started on one
process and finished on another would be refused as `invalid`, never accepted wrongly. A grant,
by contrast, survives restarts.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from sales_app.config.email_settings import (
    GmailEmailSettings,
    parse_email_settings,
)
from sales_app.config.secrets import (
    GMAIL_ACCOUNT_ADDRESS,
    GMAIL_OAUTH_CLIENT_ID,
    GMAIL_OAUTH_CLIENT_SECRET,
    GMAIL_OAUTH_STATE_KEY,
    GMAIL_REAUTH_REQUIRED,
    GMAIL_REFRESH_TOKEN,
)
from sales_app.google_mailbox import (
    NOT_CONNECTED,
    AddressAlreadyConnectedError,
    CapEnforcedByWiring,
    ConnectionStatus,
    GoogleGrant,
    GoogleMailboxConnection,
    GoogleOAuthPort,
    GoogleTokenSource,
    InMemoryPendingAuthorizationStore,
    OAuthMailChannel,
    OAuthMailInbox,
    PendingAuthorizationStore,
)
from sales_app.mailbox_access import MailboxNotReadyError, sender_name
from sales_app.secret_store import (
    GLOBAL_SCOPE,
    SecretStore,
    SecretStoreError,
    client_scope,
)
from sales_app.secret_store.errors import DuplicateSecretError
from sales_core.ports import Clock
from sales_integrations.google_oauth import (
    AccessGrant,
    GoogleOAuthClient,
    GoogleOAuthError,
    GoogleTransientError,
    TokenGrant,
)
from sales_integrations.mail import open_imap, open_smtp
from sales_integrations.mail.mailbox import OBSERVE

log = logging.getLogger(__name__)

CALLBACK_PATH = "/client/v1/channel/google/callback"
_ACTOR = "system:gmail-oauth"
UNAVAILABLE = "unavailable"

OAuthFactory = Callable[..., GoogleOAuthPort]  # (client_id=, client_secret=, redirect_uri=)


# ---- the platform's Google application ------------------------------------------------------------


@dataclass(frozen=True, slots=True, repr=False)
class GmailPlatform:
    client_id: str
    client_secret: str
    state_key: bytes
    redirect_uri: str

    def __repr__(self) -> str:
        return "GmailPlatform()"


def redirect_uri_for(public_base_url: str | None) -> str | None:
    """`{public_base_url}/client/v1/channel/google/callback`, or None when the base URL is missing
    or is not https (http is accepted for localhost only)."""
    if not public_base_url:
        return None
    parts = urlsplit(public_base_url.strip())
    local = parts.hostname in ("localhost", "127.0.0.1")
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        return None
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        return None
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}{CALLBACK_PATH}"


def _global(secrets: SecretStore, key: str) -> str | None:
    try:
        return secrets.get(GLOBAL_SCOPE, key)
    except SecretStoreError:
        return None


def platform_missing(secrets: SecretStore, public_base_url: str | None) -> list[str]:
    """What the Admin still has to set for Gmail to be offered. Names only, never a value."""
    missing = [
        label
        for key, label in (
            (GMAIL_OAUTH_CLIENT_ID, "Google OAuth client id"),
            (GMAIL_OAUTH_CLIENT_SECRET, "Google OAuth client secret"),
            (GMAIL_OAUTH_STATE_KEY, "Google OAuth state signing key"),
        )
        if not _global(secrets, key)
    ]
    if redirect_uri_for(public_base_url) is None:
        missing.append("public base URL (https)")
    return missing


def read_platform(secrets: SecretStore, public_base_url: str | None) -> GmailPlatform | None:
    client_id = _global(secrets, GMAIL_OAUTH_CLIENT_ID)
    client_secret = _global(secrets, GMAIL_OAUTH_CLIENT_SECRET)
    state_key = _global(secrets, GMAIL_OAUTH_STATE_KEY)
    redirect = redirect_uri_for(public_base_url)
    if not (client_id and client_secret and state_key and redirect):
        return None
    if len(state_key.encode("utf-8")) < 16:
        return None
    return GmailPlatform(client_id, client_secret, state_key.encode("utf-8"), redirect)


# ---- the grant, persisted ---------------------------------------------------------------------------


class SecretStoreGrantStore:
    """`GoogleGrantStore` over the encrypted secret store, in the client's own scope."""

    def __init__(self, secrets: SecretStore) -> None:
        self._secrets = secrets

    def __repr__(self) -> str:
        return "SecretStoreGrantStore()"

    def load(self, client_id: str) -> GoogleGrant | None:
        scope = client_scope(client_id)
        try:
            token = self._secrets.get(scope, GMAIL_REFRESH_TOKEN)
            address = self._secrets.get(scope, GMAIL_ACCOUNT_ADDRESS)
            reauth = self._secrets.get(scope, GMAIL_REAUTH_REQUIRED)
        except SecretStoreError:
            raise GoogleTransientError("the stored authorisation cannot be read") from None
        if not token or not address:
            return None
        return GoogleGrant(token, address, reauth == "1")

    def save(self, client_id: str, refresh_token: str, address: str) -> None:
        scope = client_scope(client_id)
        previous = self._safe_get(scope, GMAIL_ACCOUNT_ADDRESS)
        try:
            # The address first, and unique: another client holding it refuses the whole save.
            self._secrets.set(scope, GMAIL_ACCOUNT_ADDRESS, address.lower(), by=_ACTOR, unique=True)
            try:
                self._secrets.set(scope, GMAIL_REFRESH_TOKEN, refresh_token, by=_ACTOR)
            except SecretStoreError:
                if previous:
                    self._secrets.set(
                        scope, GMAIL_ACCOUNT_ADDRESS, previous, by=_ACTOR, unique=True
                    )
                else:
                    self._secrets.delete(scope, GMAIL_ACCOUNT_ADDRESS)
                raise
            self._secrets.delete(scope, GMAIL_REAUTH_REQUIRED)
        except DuplicateSecretError:
            raise AddressAlreadyConnectedError from None
        except SecretStoreError:
            raise GoogleTransientError("the authorisation cannot be kept") from None

    def mark_reauth_required(self, client_id: str) -> None:
        try:
            self._secrets.set(client_scope(client_id), GMAIL_REAUTH_REQUIRED, "1", by=_ACTOR)
        except SecretStoreError:
            log.warning("could not record that a Gmail authorisation must be renewed")

    def delete(self, client_id: str) -> None:
        scope = client_scope(client_id)
        for key in (GMAIL_REFRESH_TOKEN, GMAIL_ACCOUNT_ADDRESS, GMAIL_REAUTH_REQUIRED):
            try:
                self._secrets.delete(scope, key)
            except SecretStoreError:
                raise GoogleTransientError("the authorisation cannot be removed") from None

    def _safe_get(self, scope: str, key: str) -> str | None:
        try:
            return self._secrets.get(scope, key)
        except SecretStoreError:
            return None


# ---- OAuth resolved at each call --------------------------------------------------------------------


class ResolvingOAuth:
    """`GoogleOAuthPort` that builds the real client from the Admin's current credentials at each
    call. Not configured: a transient failure (nothing is lost; the client waits)."""

    def __init__(
        self,
        secrets: SecretStore,
        public_base_url: Callable[[], str | None],
        factory: OAuthFactory = GoogleOAuthClient,
    ) -> None:
        self._secrets = secrets
        self._base = public_base_url
        self._factory = factory

    def __repr__(self) -> str:
        return "ResolvingOAuth()"

    def _client(self) -> GoogleOAuthPort:
        platform = read_platform(self._secrets, self._base())
        if platform is None:
            raise GoogleTransientError("Google is not configured")
        return self._factory(
            client_id=platform.client_id,
            client_secret=platform.client_secret,
            redirect_uri=platform.redirect_uri,
        )

    def authorization_url(
        self, *, state: str, code_challenge: str, login_hint: str | None = None
    ) -> str:
        return self._client().authorization_url(
            state=state, code_challenge=code_challenge, login_hint=login_hint
        )

    def exchange_code(self, *, code: str, code_verifier: str) -> TokenGrant:
        return self._client().exchange_code(code=code, code_verifier=code_verifier)

    def refresh(self, refresh_token: str) -> AccessGrant:
        return self._client().refresh(refresh_token)

    def revoke(self, token: str) -> None:
        self._client().revoke(token)

    def account_address(self, access_token: str) -> str:
        return self._client().account_address(access_token)


class GoogleConnectionService:
    """The `GoogleMailboxConnection` interface, rebuilt from the current credentials per call."""

    def __init__(
        self,
        *,
        secrets: SecretStore,
        grants: SecretStoreGrantStore,
        pending: PendingAuthorizationStore,
        oauth: ResolvingOAuth,
        public_base_url: Callable[[], str | None],
        clock: Clock,
    ) -> None:
        self._secrets = secrets
        self._grants = grants
        self._pending = pending
        self._oauth = oauth
        self._base = public_base_url
        self._clock = clock

    def __repr__(self) -> str:
        return "GoogleConnectionService()"

    def configured(self) -> bool:
        return read_platform(self._secrets, self._base()) is not None

    def _connection(self) -> GoogleMailboxConnection:
        platform = read_platform(self._secrets, self._base())
        if platform is None:
            raise GoogleTransientError("Google is not configured")
        return GoogleMailboxConnection(
            oauth=self._oauth,
            grants=self._grants,
            pending=self._pending,
            signing_key=platform.state_key,
            clock=self._clock,
        )

    def start(self, client_id: str, *, login_hint: str | None = None) -> str:
        return self._connection().start(client_id, login_hint=login_hint)

    def callback(self, **kwargs: Any) -> ConnectionStatus:
        return self._connection().callback(**kwargs)

    def disconnect(self, client_id: str) -> bool:
        """Locally forgotten whatever happens; Google is asked to revoke when it is configured."""
        held = self._grants.load(client_id)
        if held is None:
            return True
        self._grants.delete(client_id)
        try:
            self._oauth.revoke(held.refresh_token)
        except GoogleOAuthError:
            return False
        return True

    def status(self, client_id: str) -> ConnectionStatus:
        try:
            held = self._grants.load(client_id)
        except GoogleTransientError:
            return ConnectionStatus(UNAVAILABLE)
        if held is None:
            return ConnectionStatus(NOT_CONNECTED)
        return ConnectionStatus(
            "reauth_required" if held.reauth_required else "connected", held.address
        )


# ---- the runtime ------------------------------------------------------------------------------------


class GmailMailService:
    """Everything the application needs for Gmail: built once by `build_application`."""

    def __init__(
        self,
        *,
        secrets: SecretStore,
        clock: Clock,
        public_base_url: Callable[[], str | None] = lambda: None,
        oauth_factory: OAuthFactory = GoogleOAuthClient,
        smtp_factory: Callable[..., Any] = open_smtp,
        imap_factory: Callable[..., Any] = open_imap,
        pending: PendingAuthorizationStore | None = None,
    ) -> None:
        self._secrets = secrets
        self._clock = clock
        self._base = public_base_url
        self._smtp = smtp_factory
        self._imap = imap_factory
        self.grants = SecretStoreGrantStore(secrets)
        self.oauth = ResolvingOAuth(secrets, public_base_url, oauth_factory)
        self.connection = GoogleConnectionService(
            secrets=secrets,
            grants=self.grants,
            pending=pending or InMemoryPendingAuthorizationStore(),
            oauth=self.oauth,
            public_base_url=public_base_url,
            clock=clock,
        )
        self._tokens: dict[str, GoogleTokenSource] = {}

    def __repr__(self) -> str:
        return "GmailMailService()"

    def configured(self) -> bool:
        return self.connection.configured()

    def missing(self) -> list[str]:
        return platform_missing(self._secrets, self._base())

    def token_source(self, client_id: str) -> GoogleTokenSource:
        source = self._tokens.get(client_id)
        if source is None:
            source = self._tokens.setdefault(
                client_id,
                GoogleTokenSource(
                    client_id=client_id, oauth=self.oauth, grants=self.grants, clock=self._clock
                ),
            )
        return source

    def _grant(self, config: Any) -> tuple[GoogleGrant, GmailEmailSettings]:
        settings = parse_email_settings(config.spec.channel_settings_for("email"))
        if not isinstance(settings, GmailEmailSettings):
            raise MailboxNotReadyError("this account's email is not a Gmail mailbox")
        try:
            grant = self.grants.load(config.client_id)
        except GoogleOAuthError:
            raise MailboxNotReadyError("the Gmail authorisation cannot be read") from None
        if grant is None:
            raise MailboxNotReadyError("no Gmail mailbox is connected to this account")
        if grant.reauth_required:
            raise MailboxNotReadyError("the Gmail authorisation must be renewed")
        return grant, settings

    def channel(self, config: Any) -> OAuthMailChannel:
        grant, settings = self._grant(config)
        return OAuthMailChannel(
            self.token_source(config.client_id),
            grant.address,
            from_name=sender_name(settings.from_name) or sender_name(config.spec.display_name),
            subject=settings.subject,
            clock=self._clock,
            smtp_factory=self._smtp,
            # `wiring.runtime._metered` counts every e-mail in the shared daily cap: not twice.
            send_guard=CapEnforcedByWiring(),
        )

    def inbox(self, config: Any) -> OAuthMailInbox:
        grant, _ = self._grant(config)
        return OAuthMailInbox(
            self.token_source(config.client_id),
            grant.address,
            read_mode=OBSERVE,
            clock=self._clock,
            imap_factory=self._imap,
        )


__all__ = [
    "GmailMailService",
    "GmailPlatform",
    "GoogleConnectionService",
    "ResolvingOAuth",
    "SecretStoreGrantStore",
    "platform_missing",
    "read_platform",
    "redirect_uri_for",
]
