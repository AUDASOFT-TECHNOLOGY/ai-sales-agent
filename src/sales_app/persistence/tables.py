"""The schema, as SQLAlchemy Core tables. The Alembic migrations are the source of truth for what
a deployed database looks like; these definitions are what the stores query with.

Only account and configuration data lives here. No credential of any kind (see
`sales_app.secret_store`) and no operational data (that is the SalesRepository's).
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """Always timezone-aware UTC in Python, whatever the database keeps (SQLite drops the zone)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


metadata = MetaData()

clients = Table(
    "clients",
    metadata,
    Column("client_id", String(64), primary_key=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String(255)),
)

client_profiles = Table(
    "client_profiles",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("login", String(64), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("must_change_password", Boolean, nullable=False),
    Column("logo", Text),
    Column("whatsapp_number", String(40)),
    # The number's digits only: what makes "+216 22 768 307" and "21622768307" the same number.
    Column("whatsapp_digits", String(20), unique=True),
    Column("whatsapp_reconnect_required", Boolean, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String(255)),
    Column("updated_at", UtcDateTime),
)

client_sessions = Table(
    "client_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column(
        "client_id", String(64), ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False
    ),
    Column("created_at", UtcDateTime, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
    Index("ix_client_sessions_client_id", "client_id"),
    Index("ix_client_sessions_expires_at", "expires_at"),
)

admin_users = Table(
    "admin_users",
    metadata,
    Column("id", String(255), primary_key=True),
    Column("username", String(255), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("role", String(16), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String(255)),
)

admin_user_clients = Table(
    "admin_user_clients",
    metadata,
    Column(
        "admin_id", String(255), ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
)

admin_sessions = Table(
    "admin_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column(
        "admin_id", String(255), ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False
    ),
    Column("created_at", UtcDateTime, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
    Index("ix_admin_sessions_admin_id", "admin_id"),
    Index("ix_admin_sessions_expires_at", "expires_at"),
)

config_versions = Table(
    "config_versions",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("parent", Integer),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("checksum", String(64), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("author", String(255), nullable=False),
    Column("note", Text, nullable=False),
)
