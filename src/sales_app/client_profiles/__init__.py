"""Client account profiles (login, hashed temporary password, logo, WhatsApp number).

Separate from `clients/<id>/client.yaml` (engine configuration) and from `admin_auth` (Revansa's own
operators). A peer of `sales_app.admin_auth`: file-backed now, port-first so a database can replace it.
"""

from sales_app.client_profiles.models import (
    LOGIN_PATTERN,
    ClientProfile,
    ClientProfileError,
    ClientProfileStoreCorruptError,
    normalize_login,
)
from sales_app.client_profiles.store import (
    ClientProfileStore,
    FileClientProfileStore,
    InMemoryClientProfileStore,
)

__all__ = [
    "LOGIN_PATTERN",
    "ClientProfile",
    "ClientProfileError",
    "ClientProfileStore",
    "ClientProfileStoreCorruptError",
    "FileClientProfileStore",
    "InMemoryClientProfileStore",
    "normalize_login",
]
