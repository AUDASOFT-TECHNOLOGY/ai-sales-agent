"""The HubSpot adapter: a read-only client for one account's contacts."""

from sales_integrations.hubspot.client import BASE_URL, HubSpotClient, HubSpotContact
from sales_integrations.hubspot.errors import (
    HubSpotAuthError,
    HubSpotDataError,
    HubSpotError,
    HubSpotRateLimitedError,
    HubSpotUnavailableError,
)

__all__ = [
    "BASE_URL",
    "HubSpotAuthError",
    "HubSpotClient",
    "HubSpotContact",
    "HubSpotDataError",
    "HubSpotError",
    "HubSpotRateLimitedError",
    "HubSpotUnavailableError",
]
