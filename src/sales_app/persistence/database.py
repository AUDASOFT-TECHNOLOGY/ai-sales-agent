"""Opening the SQL database: one SQLAlchemy engine, shared by every store (they are thread-safe).

PostgreSQL is the production target. SQLite is accepted for tests and local development only, and
nothing here depends on it: it differs from PostgreSQL in two ways this module smooths over (it
needs foreign keys switched on, and an in-memory database must keep a single connection).
"""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool


def normalize_url(url: str) -> str:
    """`postgresql://` means the psycopg 3 driver this project installs, not SQLAlchemy's default."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def create_database_engine(url: str) -> Engine:
    url = normalize_url(url)
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        # A database that cannot be reached must fail startup in seconds, not hang for minutes.
        return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10})
    in_memory = parsed.database in (None, "", ":memory:")
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool if in_memory else None,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine
