"""Google OAuth 2.0 (authorization code + PKCE) for a mailbox: consent URL, exchange, refresh, revoke."""

from sales_integrations.google_oauth.client import (
    AUTHORIZATION_URL,
    DEFAULT_SCOPES,
    IDENTITY_SCOPES,
    MAIL_SCOPE,
    REVOKE_URL,
    TOKEN_URL,
    USERINFO_URL,
    AccessGrant,
    GoogleOAuthClient,
    TokenGrant,
    code_challenge,
    new_code_verifier,
)
from sales_integrations.google_oauth.errors import (
    GoogleAccessDeniedError,
    GoogleInsufficientScopeError,
    GoogleOAuthError,
    GoogleProtocolError,
    GoogleReauthRequiredError,
    GoogleTransientError,
)

__all__ = [
    "AUTHORIZATION_URL",
    "DEFAULT_SCOPES",
    "IDENTITY_SCOPES",
    "MAIL_SCOPE",
    "REVOKE_URL",
    "TOKEN_URL",
    "USERINFO_URL",
    "AccessGrant",
    "GoogleAccessDeniedError",
    "GoogleInsufficientScopeError",
    "GoogleOAuthClient",
    "GoogleOAuthError",
    "GoogleProtocolError",
    "GoogleReauthRequiredError",
    "GoogleTransientError",
    "TokenGrant",
    "code_challenge",
    "new_code_verifier",
]
