"""The HubSpot adapter: a client for one account's contacts. It reads; it writes only the
properties the caller named when building it."""

from sales_integrations.hubspot.client import (
    BASE_URL,
    NEVER_WRITTEN,
    HubSpotClient,
    HubSpotContact,
    HubSpotProperty,
)
from sales_integrations.hubspot.errors import (
    HubSpotAuthError,
    HubSpotDataError,
    HubSpotError,
    HubSpotNotFoundError,
    HubSpotRateLimitedError,
    HubSpotUnavailableError,
)

__all__ = [
    "BASE_URL",
    "NEVER_WRITTEN",
    "HubSpotAuthError",
    "HubSpotClient",
    "HubSpotContact",
    "HubSpotDataError",
    "HubSpotError",
    "HubSpotNotFoundError",
    "HubSpotProperty",
    "HubSpotRateLimitedError",
    "HubSpotUnavailableError",
]
