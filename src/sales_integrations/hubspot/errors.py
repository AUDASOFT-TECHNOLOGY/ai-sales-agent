"""Errors raised by the HubSpot adapter.

Every message is a fixed sentence: never the access token, never a contact's name, number or address,
never what HubSpot answered. The application translates these into its own source errors.
"""


class HubSpotError(RuntimeError):
    """Base class for every adapter-specific failure."""


class HubSpotUnavailableError(HubSpotError):
    """HubSpot could not be reached, or answered with a server error. Worth retrying later."""


class HubSpotRateLimitedError(HubSpotError):
    """HubSpot refused for now. `retry_after` is the seconds it asked to wait, when it said."""

    def __init__(self, message: str = "HubSpot's rate limit was reached", *, retry_after=None):
        super().__init__(message)
        self.retry_after: float | None = retry_after


class HubSpotAuthError(HubSpotError):
    """The access token was refused, or may not read contacts. Retrying cannot help."""


class HubSpotDataError(HubSpotError):
    """HubSpot answered with something that is not what its API documents (fail closed)."""


class HubSpotNotFoundError(HubSpotError):
    """The contact asked for is not there (deleted or merged meanwhile)."""
