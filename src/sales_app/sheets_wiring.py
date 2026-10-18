"""Google Sheets access by the client's own Google authorisation (OAuth), wired to the application.

The client pastes the link of their spreadsheet, clicks "Authorise Revansa", agrees on Google's own
page and comes back: no service account, no JSON file, no shared Google account. It reuses the
mailbox plumbing (`gmail_wiring`, `google_mailbox`) with its own scope, its own callback and its own
records:

- the platform's Google application (client id, secret, state key) is the Admin's, in the global
  scope, the same one Gmail uses; only its redirect address differs (`CALLBACK_PATH`, to register
  with Google too);
- a client's grant (refresh token, Google address, `reauth_required`) lives in the encrypted secret
  store, in that client's scope alone (`sheets.*` keys, not in the Admin catalog, never returned);
- an access token is requested when needed and never kept beyond its life.

Revansa only ever reads and writes the one spreadsheet the client saved in their source settings;
the Google scope itself (`spreadsheets`) is not limited to one file, which Google only offers
through a file picker (see docs/google-oauth-channel.md).
"""

from collections.abc import Callable
from typing import Any

from sales_app.config.secrets import (
    SHEETS_ACCOUNT_ADDRESS,
    SHEETS_REAUTH_REQUIRED,
    SHEETS_REFRESH_TOKEN,
)
from sales_app.gmail_wiring import (
    GoogleConnectionService,
    GrantKeys,
    OAuthFactory,
    ResolvingOAuth,
    SecretStoreGrantStore,
    platform_missing,
)
from sales_app.google_mailbox import (
    GoogleTokenSource,
    InMemoryPendingAuthorizationStore,
    PendingAuthorizationStore,
)
from sales_app.secret_store import SecretStore
from sales_core.ports import Clock
from sales_integrations.google_oauth import SHEETS_SCOPE, SHEETS_SCOPES, GoogleOAuthClient

CALLBACK_PATH = "/client/v1/sheets-access/callback"
SHEETS_GRANT_KEYS = GrantKeys(
    refresh_token=SHEETS_REFRESH_TOKEN,
    address=SHEETS_ACCOUNT_ADDRESS,
    reauth=SHEETS_REAUTH_REQUIRED,
)
_ACTOR = "system:sheets-oauth"


def _sheets_oauth_client(**platform: Any) -> GoogleOAuthClient:
    return GoogleOAuthClient(**platform, scopes=SHEETS_SCOPES)


class SheetsAccessService:
    """Everything the application needs for Sheets by OAuth: built once by `build_application`."""

    def __init__(
        self,
        *,
        secrets: SecretStore,
        clock: Clock,
        public_base_url: Callable[[], str | None] = lambda: None,
        oauth_factory: OAuthFactory | None = None,
        pending: PendingAuthorizationStore | None = None,
        sheets_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._secrets = secrets
        self._clock = clock
        self._base = public_base_url
        self._sheets = sheets_factory
        self.grants = SecretStoreGrantStore(
            secrets, SHEETS_GRANT_KEYS, unique_address=False, actor=_ACTOR
        )
        self.oauth = ResolvingOAuth(
            secrets,
            public_base_url,
            oauth_factory or _sheets_oauth_client,
            callback_path=CALLBACK_PATH,
        )
        self.connection = GoogleConnectionService(
            secrets=secrets,
            grants=self.grants,
            pending=pending or InMemoryPendingAuthorizationStore(),
            oauth=self.oauth,
            public_base_url=public_base_url,
            clock=clock,
            callback_path=CALLBACK_PATH,
            required_scopes=(SHEETS_SCOPE,),
        )
        self._tokens: dict[str, GoogleTokenSource] = {}

    def __repr__(self) -> str:
        return "SheetsAccessService()"

    def configured(self) -> bool:
        return self.connection.configured()

    def missing(self) -> list[str]:
        return platform_missing(self._secrets, self._base(), CALLBACK_PATH)

    def token_source(self, client_id: str) -> GoogleTokenSource:
        source = self._tokens.get(client_id)
        if source is None:
            source = self._tokens.setdefault(
                client_id,
                GoogleTokenSource(
                    client_id=client_id,
                    oauth=self.oauth,
                    grants=self.grants,
                    clock=self._clock,
                    required_scopes=(SHEETS_SCOPE,),
                ),
            )
        return source

    def api(self, client_id: str, *, timeout_seconds: float | None = None, max_attempts: int = 5):
        """The client's Sheets API over their own authorisation."""
        factory = self._sheets
        if factory is None:
            # The Google adapter is the business of its provider's descriptor alone.
            from sales_app.wiring.providers import google_sheets

            factory = google_sheets.open_oauth_api
        return factory(
            self.token_source(client_id), timeout_seconds=timeout_seconds, max_attempts=max_attempts
        )


# The running application installs its service here, so that the provider's build functions (which
# receive a client's configuration and nothing else) can reach the client's authorisation.
_installed: SheetsAccessService | None = None


def install(service: SheetsAccessService | None) -> None:
    global _installed
    _installed = service


def installed() -> SheetsAccessService | None:
    return _installed


__all__ = ["CALLBACK_PATH", "SheetsAccessService", "install", "installed"]
