"""The one place that decides where accounts, sessions and settings live: the database when
`SALES_AGENT_DATABASE_URL` is set, the JSON files otherwise (the fallback kept during the move to a
database). The application, `sales-agent admin` and `sales-agent config` all build their stores here,
so they can never disagree about where the data is."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine

from sales_app.admin_auth import (
    AdminSessionStore,
    AdminUserStore,
    FileAdminSessionStore,
    FileAdminUserStore,
)
from sales_app.agent_state import AgentStateStore, FileAgentStateStore
from sales_app.client_profiles import ClientProfileStore, FileClientProfileStore
from sales_app.config_store import ConfigStore, FileConfigStore
from sales_app.errors import ConfigurationError
from sales_app.persistence.admin_stores import SqlAdminSessionStore, SqlAdminUserStore
from sales_app.persistence.agent_state_store import SqlAgentStateStore
from sales_app.persistence.client_stores import SqlClientProfileStore, SqlClientSessionStore
from sales_app.persistence.clients import seed_clients
from sales_app.persistence.database import create_database_engine
from sales_app.persistence.schema import SchemaNotReadyError, ensure_schema_current
from sales_app.persistence.sql_config_store import SqlConfigStore
from sales_app.settings import Settings


@dataclass(frozen=True)
class AccountStores:
    admin_users: AdminUserStore
    admin_sessions: AdminSessionStore
    client_profiles: ClientProfileStore
    client_sessions: AdminSessionStore
    config_store: ConfigStore
    agent_state: AgentStateStore
    engine: Engine | None = None  # None: the file fallback

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()


def open_engine(settings: Settings) -> Engine | None:
    if settings.database_url is None:
        return None
    return create_database_engine(settings.database_url.get_secret_value())


def build_account_stores(settings: Settings) -> AccountStores:
    engine = open_engine(settings)
    if engine is None:
        return AccountStores(
            admin_users=FileAdminUserStore(settings.admin_dir / "users.json"),
            admin_sessions=FileAdminSessionStore(settings.admin_dir / "sessions"),
            client_profiles=FileClientProfileStore(settings.profiles_dir / "profiles.json"),
            client_sessions=FileAdminSessionStore(settings.profiles_dir / "sessions"),
            config_store=FileConfigStore(settings.config_dir),
            agent_state=FileAgentStateStore(settings.profiles_dir / "agent_state.json"),
        )
    try:
        ensure_schema_current(engine)
        # Every client directory is a client: this is what lets an Admin be scoped to it.
        seed_clients(engine, settings.clients_dir, now=datetime.now(UTC))
    except SchemaNotReadyError as error:
        engine.dispose()
        raise ConfigurationError([str(error)]) from error
    except Exception as error:
        engine.dispose()
        raise ConfigurationError(
            [f"cannot use the database: {error.__class__.__name__}"]
        ) from error
    return AccountStores(
        admin_users=SqlAdminUserStore(engine),
        admin_sessions=SqlAdminSessionStore(engine),
        client_profiles=SqlClientProfileStore(engine),
        client_sessions=SqlClientSessionStore(engine),
        config_store=SqlConfigStore(engine),
        agent_state=SqlAgentStateStore(engine),
        engine=engine,
    )
