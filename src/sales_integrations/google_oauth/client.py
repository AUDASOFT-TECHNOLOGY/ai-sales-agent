"""Google's OAuth 2.0 endpoints for one OAuth application (authorization code + PKCE, offline).

What it does: build the consent URL, exchange a code, refresh an access token, revoke a token, read
the address of the account. It holds no state and stores nothing. Secrets (client secret, tokens,
codes, verifiers) travel only in request bodies or headers: never in a URL we log, a `repr`, an
exception message or a log line (nothing here logs). A failure is a fixed sentence from `errors`.

The mailbox scope is isolated in `MAIL_SCOPE`: `https://mail.google.com/` is the only scope that
opens IMAP/SMTP with XOAUTH2. A later Gmail REST implementation replaces it through `scopes=`.
"""

import base64
import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from sales_integrations.google_oauth.errors import (
    GoogleAccessDeniedError,
    GoogleOAuthError,
    GoogleProtocolError,
    GoogleReauthRequiredError,
    GoogleTransientError,
)

AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

MAIL_SCOPE = "https://mail.google.com/"  # IMAP + SMTP over XOAUTH2 (a restricted scope)
IDENTITY_SCOPES = ("openid", "email")  # only to read the address of the account
DEFAULT_SCOPES = (MAIL_SCOPE, *IDENTITY_SCOPES)
DEFAULT_TIMEOUT_SECONDS = 15.0


def new_code_verifier() -> str:
    """A fresh PKCE verifier (RFC 7636: 43 to 128 unreserved characters)."""
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    """The S256 challenge of a verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True, repr=False)
class TokenGrant:
    """What the code exchange returns. Tokens are out of `repr`."""

    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_in: int
    scope: str = ""

    def __repr__(self) -> str:
        return "TokenGrant()"


@dataclass(frozen=True, slots=True, repr=False)
class AccessGrant:
    """What a refresh returns."""

    access_token: str = field(repr=False)
    expires_in: int
    scope: str = ""

    def __repr__(self) -> str:
        return "AccessGrant()"


class GoogleOAuthClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: Sequence[str] = DEFAULT_SCOPES,
        http_client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not client_id or not client_secret or not redirect_uri:
            raise ValueError("a client id, a client secret and a redirect URI are required")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._scopes = tuple(scopes)
        self._http = http_client
        self._timeout = timeout

    def __repr__(self) -> str:
        return "GoogleOAuthClient()"

    # ---- the consent URL ---------------------------------------------------------------------

    def authorization_url(
        self, *, state: str, code_challenge: str, login_hint: str | None = None
    ) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
        }
        if login_hint:
            params["login_hint"] = login_hint
        return f"{AUTHORIZATION_URL}?{urlencode(params)}"

    # ---- the token endpoint ------------------------------------------------------------------

    def exchange_code(self, *, code: str, code_verifier: str) -> TokenGrant:
        body = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": self._redirect_uri,
            }
        )
        access = _text(body, "access_token")
        refresh = body.get("refresh_token")
        if refresh is not None and (not isinstance(refresh, str) or not refresh):
            raise GoogleProtocolError("Google's token answer was not understood")
        return TokenGrant(
            access_token=access,
            refresh_token=refresh,
            expires_in=_seconds(body),
            scope=str(body.get("scope") or ""),
        )

    def refresh(self, refresh_token: str) -> AccessGrant:
        if not refresh_token:
            raise GoogleReauthRequiredError("the Google authorisation must be renewed")
        body = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
        return AccessGrant(
            access_token=_text(body, "access_token"),
            expires_in=_seconds(body),
            scope=str(body.get("scope") or ""),
        )

    def revoke(self, token: str) -> None:
        """Revoke a refresh or access token. A token Google no longer knows counts as revoked."""
        response = self._request("POST", REVOKE_URL, data={"token": token})
        if response.status_code == 200:
            return
        if response.status_code == 400 and _error_code(response) == "invalid_token":
            return
        raise self._failure(response)

    def account_address(self, access_token: str) -> str:
        response = self._request(
            "GET", USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        if response.status_code != 200:
            raise self._failure(response)
        body = _json(response)
        address = body.get("email")
        if (
            not isinstance(address, str)
            or "@" not in address
            or body.get("email_verified") is False
        ):
            raise GoogleProtocolError("Google's account answer was not understood")
        return address.strip().lower()

    # ---- internals ---------------------------------------------------------------------------

    def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        data = {**form, "client_id": self._client_id, "client_secret": self._client_secret}
        response = self._request("POST", TOKEN_URL, data=data)
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
            raise GoogleTransientError("Google could not be reached") from None

    def _failure(self, response: httpx.Response) -> GoogleOAuthError:
        status = response.status_code
        if status >= 500 or status == 429:
            return GoogleTransientError("Google is unavailable")
        code = _error_code(response)
        if code == "invalid_grant":
            return GoogleReauthRequiredError("the Google authorisation must be renewed")
        if code == "access_denied" or status in (401, 403):
            return GoogleAccessDeniedError("Google access was refused")
        return GoogleProtocolError("Google refused the request")


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise GoogleProtocolError("Google's answer was not understood") from None
    if not isinstance(body, dict):
        raise GoogleProtocolError("Google's answer was not understood")
    return body


def _error_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    value = body.get("error") if isinstance(body, dict) else None
    return value if isinstance(value, str) else ""


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise GoogleProtocolError("Google's token answer was not understood")
    return value


def _seconds(body: dict[str, Any]) -> int:
    value = body.get("expires_in")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GoogleProtocolError("Google's token answer was not understood")
    return value
