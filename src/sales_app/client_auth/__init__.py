"""Client authentication: login, session and password change for the accounts Revansa manages.

A peer of `sales_app.admin_auth` (it reuses its hashing, token and session-store parts) and of
`sales_app.client_profiles`, which holds the accounts. Port-first like them: the profile and session
stores are injected, so a database can replace the files without touching this package.
"""

from sales_app.client_auth.service import (
    ClientAuthService,
    ClientInactiveError,
    ClientLoginResult,
    WrongCurrentPasswordError,
)
from sales_app.client_auth.status import is_client_active, stored_display_name

__all__ = [
    "ClientAuthService",
    "ClientInactiveError",
    "ClientLoginResult",
    "WrongCurrentPasswordError",
    "is_client_active",
    "stored_display_name",
]
