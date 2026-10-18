"""Errors raised by the Google OAuth adapter.

Every message is a fixed sentence: never a token, a code, a client secret, an address, or what Google
answered. The application translates these into its own states.
"""


class GoogleOAuthError(RuntimeError):
    """Base class for every adapter-specific failure."""


class GoogleReauthRequiredError(GoogleOAuthError):
    """`invalid_grant`: the refresh token is revoked, expired or replaced. Only a new consent helps."""


class GoogleAccessDeniedError(GoogleOAuthError):
    """The person refused the consent (`access_denied`), or Google refused the app itself."""


class GoogleInsufficientScopeError(GoogleOAuthError):
    """The person consented but did not grant what reading and sending mail need (Google's granular
    consent lets a person untick a permission). Nothing is kept; a new consent must grant all."""


class GoogleTransientError(GoogleOAuthError):
    """Google could not be reached, or answered 5xx / 429. Worth retrying later; nothing is lost."""


class GoogleProtocolError(GoogleOAuthError):
    """Google answered with something its documentation does not describe (fail closed)."""
