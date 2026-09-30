"""Admin Revansa authentication and authorisation: accounts, sessions, roles.

This package knows nothing about clients' data or configuration -- only who may reach
`/admin/v1` and for which client ids. It is a peer of `sales_app.config_store`: the same
file-backed-now, port-first-so-a-database-can-replace-it-later shape.
"""

from sales_app.admin_auth.models import (
    AdminRole,
    AdminSession,
    AdminStoreCorruptError,
    AdminStoreError,
    AdminUser,
    InvalidCredentialsError,
    TooManyAttemptsError,
)
from sales_app.admin_auth.service import AdminAuthService, LoginResult
from sales_app.admin_auth.session_store import (
    AdminSessionStore,
    FileAdminSessionStore,
    InMemoryAdminSessionStore,
)
from sales_app.admin_auth.user_store import (
    AdminUserStore,
    FileAdminUserStore,
    InMemoryAdminUserStore,
)

__all__ = [
    "AdminAuthService",
    "AdminRole",
    "AdminSession",
    "AdminSessionStore",
    "AdminStoreCorruptError",
    "AdminStoreError",
    "AdminUser",
    "AdminUserStore",
    "FileAdminSessionStore",
    "FileAdminUserStore",
    "InMemoryAdminSessionStore",
    "InMemoryAdminUserStore",
    "InvalidCredentialsError",
    "LoginResult",
    "TooManyAttemptsError",
]
