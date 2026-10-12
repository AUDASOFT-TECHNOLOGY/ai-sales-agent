"""The schema, as SQLAlchemy Core tables. The Alembic migrations are the source of truth for what
a deployed database looks like; these definitions are what the stores query with.

Account and configuration data, and (the `op_*` tables) a client's operational data when its
prospects live in another system. No credential of any kind (see `sales_app.secret_store`).
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
    UniqueConstraint,
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

# The Client's own switch for its agent (run or pause). Not the Admin's `enabled`, which stays in
# `client.yaml`: the two are never one value.
client_agent_state = Table(
    "client_agent_state",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("running", Boolean, nullable=False),
    Column("changed_at", UtcDateTime, nullable=False),
    Column("changed_by", String(255), nullable=False),
)

# The last real test of a client's prospect source, with a fingerprint of what it was made for: a
# result counts only while the source and its connection parameters are still those.
client_source_check = Table(
    "client_source_check",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("ok", Boolean, nullable=False),
    Column("checked_at", UtcDateTime, nullable=False),
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

# ---- a client's operational data (see `sales_app.persistence.operational_rows`) ------------------
#
# What Revansa does for one client when its prospects live elsewhere: actions, messages, the audit
# trail, facts, and its own state for each prospect. Every row carries `client_id` (a foreign key to
# `clients`) and every query filters on it. The full record is kept as a JSON `document`; the other
# columns are only what is queried, ordered or kept unique. No credential is ever stored here.

op_actions = Table(
    "op_actions",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("action_id", String(128), primary_key=True),
    Column("idempotency_key", String(255), nullable=False),
    Column("status", String(32), nullable=False),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    UniqueConstraint("client_id", "idempotency_key", name="uq_op_actions_idempotency_key"),
    Index("ix_op_actions_status", "client_id", "status"),
)

op_messages = Table(
    "op_messages",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),  # append order
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("message_id", String(128), nullable=False),
    Column("prospect_id", String(255), nullable=False),
    Column("conversation_id", String(255), nullable=False),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    UniqueConstraint("client_id", "message_id", name="uq_op_messages_message_id"),
    Index("ix_op_messages_prospect", "client_id", "prospect_id"),
)

op_audit = Table(
    "op_audit",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("occurred_at", UtcDateTime, nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("prospect_id", String(255)),
    Column("action_id", String(128)),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Index("ix_op_audit_prospect", "client_id", "prospect_id"),
)

op_facts = Table(
    "op_facts",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("fact_id", String(64), nullable=False),  # `fact_identity`: a retry is ignored
    Column("prospect_id", String(255), nullable=False),
    Column("recorded_at", UtcDateTime, nullable=False),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    UniqueConstraint("client_id", "fact_id", name="uq_op_facts_fact_id"),
    Index("ix_op_facts_prospect", "client_id", "prospect_id"),
)

op_prospect_states = Table(
    "op_prospect_states",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("prospect_id", String(255), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("conversation_id", String(255)),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
)

op_local_prospects = Table(
    "op_local_prospects",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("client_id", String(64), ForeignKey("clients.client_id"), nullable=False),
    Column("prospect_id", String(255), nullable=False),
    Column("document", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    UniqueConstraint("client_id", "prospect_id", name="uq_op_local_prospects_prospect_id"),
)

# One row per comparable contact key of a local prospect: the database itself refuses two local
# prospects that share a phone number or an address.
op_local_contacts = Table(
    "op_local_contacts",
    metadata,
    Column("client_id", String(64), ForeignKey("clients.client_id"), primary_key=True),
    Column("contact_key", String(320), primary_key=True),
    Column("prospect_id", String(255), nullable=False),
)
