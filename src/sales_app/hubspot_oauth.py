"""HubSpot's OAuth 2.0 endpoints for one HubSpot application (authorization code, refresh tokens).

The same shape as `sales_integrations.google_oauth.GoogleOAuthClient`, so that the connection
machinery written for Google (signed single-use state, persistent grant, token source that renews
itself, `reauth_required`) serves HubSpot unchanged: it speaks the `GoogleOAuthPort` and raises the
same typed errors (their names are historical; they mean "the OAuth provider ...").

What HubSpot does differently, and what this class does about it:

- no PKCE: the verifier it is handed is not sent (the signed `state` and the exact redirect URI are
  what bind the answer to the client);
- the token answer does not list the granted scopes: they are read from the access-token
  information endpoint right after the exchange, so a missing permission is still refused;
- an access token lives 30 minutes; a refresh token does not expire until it is revoked;
- the account is identified by its portal (hub) id and the user who consented.

Secrets (client secret, tokens, codes) travel only in request bodies, headers or the path HubSpot
documents for revocation; they are never in an exception message, a `repr` or a log. Nothing here
logs. NOT verified against a real HubSpot account: the tests use `httpx.MockTransport`.
"""

from collections.abc import Sequence
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from sales_integrations.google_oauth import (
    AccessGrant,
    GoogleAccessDeniedError,
    GoogleOAuthError,
    GoogleProtocolError,
    GoogleReauthRequiredError,
    GoogleTransientError,
    TokenGrant,
)

AUTHORIZATION_URL = "https://app.hubspot.com/oauth/authorize"
API_URL = "https://api.hubapi.com"
TOKEN_PATH = "/oauth/v1/token"
TOKEN_INFO_PATH = "/oauth/v1/access-tokens/"
REFRESH_TOKEN_PATH = "/oauth/v1/refresh-tokens/"

# Read the contacts, update the properties Revansa mirrors, read the property definitions to check
# them before any write. All three are asked: HubSpot lists them on its consent page.
CONTACTS_READ = "crm.objects.contacts.read"
CONTACTS_WRITE = "crm.objects.contacts.write"
SCHEMA_READ = "crm.schemas.contacts.read"
HUBSPOT_SCOPES = (CONTACTS_READ, CONTACTS_WRITE, SCHEMA_READ)
DEFAULT_TIMEOUT_SECONDS = 15.0

_REAUTH_STATUSES = frozenset({"BAD_REFRESH_TOKEN", "invalid_grant", "EXPIRED_REFRESH_TOKEN"})


class HubSpotOAuthClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: Sequence[str] = HUBSPOT_SCOPES,
        authorization_url: str = AUTHORIZATION_URL,
        api_url: str = API_URL,
        http_client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not client_id or not client_secret or not redirect_uri:
            raise ValueError("a client id, a client secret and a redirect URI are required")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._scopes = tuple(scopes)
        self._authorization_url = authorization_url
        self._api = api_url.rstrip("/")
        self._http = http_client
        self._timeout = timeout

    def __repr__(self) -> str:
        return "HubSpotOAuthClient()"

    # ---- the consent URL ---------------------------------------------------------------------

    def authorization_url(
        self, *, state: str, code_challenge: str, login_hint: str | None = None
    ) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": " ".join(self._scopes),
            "state": state,
        }
        return f"{self._authorization_url}?{urlencode(params, quote_via=quote)}"

    # ---- the token endpoint ------------------------------------------------------------------

    def exchange_code(self, *, code: str, code_verifier: str) -> TokenGrant:
        body = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
            }
        )
        access = _text(body, "access_token")
        refresh = body.get("refresh_token")
        if refresh is not None and (not isinstance(refresh, str) or not refresh):
            raise GoogleProtocolError("HubSpot's token answer was not understood")
        scopes = self._token_info(access).get("scopes")
        granted = (
            " ".join(s for s in scopes if isinstance(s, str)) if isinstance(scopes, list) else ""
        )
        return TokenGrant(
            access_token=access,
            refresh_token=refresh,
            expires_in=_seconds(body),
            scope=granted,
        )

    def refresh(self, refresh_token: str) -> AccessGrant:
        if not refresh_token:
            raise GoogleReauthRequiredError("the HubSpot authorisation must be renewed")
        body = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
        return AccessGrant(access_token=_text(body, "access_token"), expires_in=_seconds(body))

    def revoke(self, token: str) -> None:
        """Revoke a refresh token. One HubSpot no longer knows counts as revoked."""
        response = self._request(
            "DELETE", f"{self._api}{REFRESH_TOKEN_PATH}{quote(token, safe='')}"
        )
        if response.status_code in (200, 204, 400, 404):
            return
        raise self._failure(response)

    def account_address(self, access_token: str) -> str:
        """Who consented and for which account, as one line shown to that client only."""
        info = self._token_info(access_token)
        hub_id = info.get("hub_id")
        user = info.get("user")
        if isinstance(hub_id, bool) or not isinstance(hub_id, int | str) or not str(hub_id):
            raise GoogleProtocolError("HubSpot's account answer was not understood")
        label = f"portal {hub_id}"
        if isinstance(user, str) and user.strip():
            label = f"{user.strip()} (portal {hub_id})"
        return label.lower()

    # ---- internals ---------------------------------------------------------------------------

    def _token_info(self, access_token: str) -> dict[str, Any]:
        response = self._request(
            "GET", f"{self._api}{TOKEN_INFO_PATH}{quote(access_token, safe='')}"
        )
        if response.status_code != 200:
            raise self._failure(response)
        return _json(response)

    def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        data = {**form, "client_id": self._client_id, "client_secret": self._client_secret}
        response = self._request("POST", f"{self._api}{TOKEN_PATH}", data=data)
        if response.status_code != 200:
            raise self._failure(response)
        return _json(response)

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            if self._http is not None:
                return self._http.request(method, url, **kwargs)
            with httpx.Client(timeout=self._timeout) as http:
                return http.request(method, url, **kwargs)
        except httpx.HTTPError:
            raise GoogleTransientError("HubSpot could not be reached") from None

    def _failure(self, response: httpx.Response) -> GoogleOAuthError:
        status = response.status_code
        if status >= 500 or status == 429:
            return GoogleTransientError("HubSpot is unavailable")
        if _error_code(response) in _REAUTH_STATUSES:
            return GoogleReauthRequiredError("the HubSpot authorisation must be renewed")
        if status in (401, 403):
            return GoogleAccessDeniedError("HubSpot access was refused")
        return GoogleProtocolError("HubSpot refused the request")


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise GoogleProtocolError("HubSpot's answer was not understood") from None
    if not isinstance(body, dict):
        raise GoogleProtocolError("HubSpot's answer was not understood")
    return body


def _error_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    for key in ("status", "error"):
        value = body.get(key)
        if isinstance(value, str):
            return value
    return ""


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise GoogleProtocolError("HubSpot's token answer was not understood")
    return value


def _seconds(body: dict[str, Any]) -> int:
    value = body.get("expires_in")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GoogleProtocolError("HubSpot's token answer was not understood")
    return value
