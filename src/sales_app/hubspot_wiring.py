"""HubSpot access by the client's own HubSpot authorisation (OAuth), wired to the application.

The client clicks "Connect HubSpot", agrees on HubSpot's own page and comes back: no Private App, no
token to copy. It reuses the connection machinery of Google (`gmail_wiring`, `google_mailbox`) with
HubSpot's endpoints (`hubspot_oauth`), its own callback and its own records:

- the platform's HubSpot application (client id, secret, state key) is the Admin's, in the global
  scope; its redirect address is `CALLBACK_PATH`, to register in the HubSpot developer account;
- a client's grant (refresh token, account, `reauth_required`) lives in the encrypted secret store,
  in that client's scope alone (`hubspot.*` keys, not in the Admin catalog, never returned);
- an access token (30 minutes) is requested when needed and never kept beyond its life, per client.

The Private App token the Admin could enter before still works as a fallback for a client that has
no grant of its own.
"""

from collections.abc import Callable

from sales_app.config.secrets import (
    HUBSPOT_ACCOUNT_ADDRESS,
    HUBSPOT_OAUTH_CLIENT_ID,
    HUBSPOT_OAUTH_CLIENT_SECRET,
    HUBSPOT_OAUTH_STATE_KEY,
    HUBSPOT_REAUTH_REQUIRED,
    HUBSPOT_REFRESH_TOKEN,
)
from sales_app.gmail_wiring import (
    GoogleConnectionService,
    GrantKeys,
    OAuthFactory,
    PlatformKeys,
    ResolvingOAuth,
    SecretStoreGrantStore,
    platform_missing,
)
from sales_app.google_mailbox import (
    GoogleTokenSource,
    InMemoryPendingAuthorizationStore,
    PendingAuthorizationStore,
)
from sales_app.hubspot_oauth import CONTACTS_READ, SCHEMA_READ, HubSpotOAuthClient
from sales_app.secret_store import SecretStore
from sales_core.ports import Clock
from sales_integrations.google_oauth import GoogleOAuthError, GoogleReauthRequiredError

CALLBACK_PATH = "/client/v1/hubspot-access/callback"
HUBSPOT_PLATFORM_KEYS = PlatformKeys(
    client_id=HUBSPOT_OAUTH_CLIENT_ID,
    client_secret=HUBSPOT_OAUTH_CLIENT_SECRET,
    state_key=HUBSPOT_OAUTH_STATE_KEY,
    id_label="HubSpot app client id",
    secret_label="HubSpot app client secret",
    state_label="HubSpot OAuth state signing key",
)
HUBSPOT_GRANT_KEYS = GrantKeys(
    refresh_token=HUBSPOT_REFRESH_TOKEN,
    address=HUBSPOT_ACCOUNT_ADDRESS,
    reauth=HUBSPOT_REAUTH_REQUIRED,
)
# What a grant must hold to be usable: reading contacts and the property definitions. Writing is
# asked too, but a client that withheld it still reads (the write-back then reports itself refused).
REQUIRED_SCOPES = (CONTACTS_READ, SCHEMA_READ)
_ACTOR = "system:hubspot-oauth"


class HubSpotAccessError(Exception):
    """The client's HubSpot access token cannot be given now. `reauthorise`: the client must connect
    again (revoked, never connected); otherwise it is temporary. A fixed sentence, never a token."""

    def __init__(self, *, reauthorise: bool) -> None:
        super().__init__(
            "HubSpot must be authorised again"
            if reauthorise
            else "HubSpot access is temporarily unavailable"
        )
        self.reauthorise = reauthorise


class HubSpotAccessService:
    """Everything the application needs for HubSpot by OAuth: built once by `build_application`."""

    def __init__(
        self,
        *,
        secrets: SecretStore,
        clock: Clock,
        public_base_url: Callable[[], str | None] = lambda: None,
        oauth_factory: OAuthFactory | None = None,
        pending: PendingAuthorizationStore | None = None,
    ) -> None:
        self._secrets = secrets
        self._clock = clock
        self._base = public_base_url
        self.grants = SecretStoreGrantStore(
            secrets, HUBSPOT_GRANT_KEYS, unique_address=False, actor=_ACTOR
        )
        self.oauth = ResolvingOAuth(
            secrets,
            public_base_url,
            oauth_factory or HubSpotOAuthClient,
            callback_path=CALLBACK_PATH,
            platform_keys=HUBSPOT_PLATFORM_KEYS,
        )
        self.connection = GoogleConnectionService(
            secrets=secrets,
            grants=self.grants,
            pending=pending or InMemoryPendingAuthorizationStore(),
            oauth=self.oauth,
            public_base_url=public_base_url,
            clock=clock,
            callback_path=CALLBACK_PATH,
            required_scopes=REQUIRED_SCOPES,
            platform_keys=HUBSPOT_PLATFORM_KEYS,
        )
        self._tokens: dict[str, GoogleTokenSource] = {}

    def __repr__(self) -> str:
        return "HubSpotAccessService()"

    def configured(self) -> bool:
        return self.connection.configured()

    def missing(self) -> list[str]:
        return platform_missing(self._secrets, self._base(), CALLBACK_PATH, HUBSPOT_PLATFORM_KEYS)

    def access_token(self, client_id: str) -> str:
        """A fresh access token for this client, or `HubSpotAccessError`."""
        try:
            return self.token_source(client_id).access_token()
        except GoogleReauthRequiredError:
            raise HubSpotAccessError(reauthorise=True) from None
        except GoogleOAuthError:
            raise HubSpotAccessError(reauthorise=False) from None

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
                    required_scopes=(),  # a refresh does not list scopes: they are checked at consent
                ),
            )
        return source


# The running application installs its service here, so that the provider's build functions (which
# receive a client's configuration and nothing else) can reach the client's authorisation.
_installed: HubSpotAccessService | None = None


def install(service: HubSpotAccessService | None) -> None:
    global _installed
    _installed = service


def installed() -> HubSpotAccessService | None:
    return _installed


__all__ = ["CALLBACK_PATH", "HubSpotAccessError", "HubSpotAccessService", "install", "installed"]
