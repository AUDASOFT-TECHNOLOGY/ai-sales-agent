"""SQL persistence for accounts, sessions and versioned client settings.

The only package of `sales_app` allowed to import a database library (an architecture test pins
that). It implements ports that live elsewhere (`AdminUserStore`, `AdminSessionStore`,
`ClientProfileStore`, `ConfigStore`) so nothing above it knows whether data is in files or in SQL.
PostgreSQL is the production target; SQLite is for tests and local development only.

Not stored here, by design: credentials of any kind (`sales_app.secret_store` keeps those), and
operational data (the SalesRepository's).
"""

from sales_app.persistence.admin_stores import SqlAdminSessionStore, SqlAdminUserStore
from sales_app.persistence.agent_state_store import SqlAgentStateStore
from sales_app.persistence.client_stores import SqlClientProfileStore, SqlClientSessionStore
from sales_app.persistence.clients import seed_clients, table_counts
from sales_app.persistence.database import create_database_engine
from sales_app.persistence.factory import AccountStores, build_account_stores
from sales_app.persistence.import_files import ImportReport, import_file_data
from sales_app.persistence.schema import (
    SchemaNotReadyError,
    current_revision,
    ensure_schema_current,
    head_revision,
    upgrade_database,
)
from sales_app.persistence.source_check_store import SqlSourceCheckStore
from sales_app.persistence.sql_config_store import SqlConfigStore

__all__ = [
    "AccountStores",
    "ImportReport",
    "SchemaNotReadyError",
    "SqlAdminSessionStore",
    "SqlAdminUserStore",
    "SqlAgentStateStore",
    "SqlClientProfileStore",
    "SqlClientSessionStore",
    "SqlConfigStore",
    "SqlSourceCheckStore",
    "build_account_stores",
    "create_database_engine",
    "current_revision",
    "ensure_schema_current",
    "head_revision",
    "import_file_data",
    "seed_clients",
    "table_counts",
    "upgrade_database",
]
