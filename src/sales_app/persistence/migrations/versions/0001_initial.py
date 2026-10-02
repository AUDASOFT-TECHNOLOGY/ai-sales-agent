"""Accounts, sessions and versioned settings.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_UTC = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("client_id", sa.String(64), primary_key=True),
        sa.Column("created_at", _UTC, nullable=False),
        sa.Column("created_by", sa.String(255)),
    )
    op.create_table(
        "client_profiles",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("login", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("must_change_password", sa.Boolean, nullable=False),
        sa.Column("logo", sa.Text),
        sa.Column("whatsapp_number", sa.String(40)),
        sa.Column("whatsapp_digits", sa.String(20), unique=True),
        sa.Column("whatsapp_reconnect_required", sa.Boolean, nullable=False),
        sa.Column("created_at", _UTC, nullable=False),
        sa.Column("created_by", sa.String(255)),
        sa.Column("updated_at", _UTC),
    )
    op.create_table(
        "client_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(64),
            sa.ForeignKey("clients.client_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", _UTC, nullable=False),
        sa.Column("expires_at", _UTC, nullable=False),
    )
    op.create_index("ix_client_sessions_client_id", "client_sessions", ["client_id"])
    op.create_index("ix_client_sessions_expires_at", "client_sessions", ["expires_at"])
    op.create_table(
        "admin_users",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("username", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("created_at", _UTC, nullable=False),
        sa.Column("created_by", sa.String(255)),
        sa.CheckConstraint(
            "role in ('owner', 'operator', 'editor', 'viewer')", name="ck_admin_users_role"
        ),
    )
    op.create_table(
        "admin_user_clients",
        sa.Column(
            "admin_id",
            sa.String(255),
            sa.ForeignKey("admin_users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "admin_id",
            sa.String(255),
            sa.ForeignKey("admin_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", _UTC, nullable=False),
        sa.Column("expires_at", _UTC, nullable=False),
    )
    op.create_index("ix_admin_sessions_admin_id", "admin_sessions", ["admin_id"])
    op.create_index("ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"])
    op.create_table(
        "config_versions",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("parent", sa.Integer),
        sa.Column("document", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", _UTC, nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("note", sa.Text, nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_config_versions_version"),
    )
    _make_config_versions_append_only()


def _make_config_versions_append_only() -> None:
    """A published version is history: the database itself refuses to change or remove it."""
    message = "config_versions is append-only"
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION config_versions_append_only() RETURNS trigger AS $$ "
            f"BEGIN RAISE EXCEPTION '{message}'; END; $$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER config_versions_no_change BEFORE UPDATE OR DELETE ON config_versions "
            "FOR EACH ROW EXECUTE FUNCTION config_versions_append_only()"
        )
    else:
        for event in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER config_versions_no_{event.lower()} BEFORE {event} "
                f"ON config_versions BEGIN SELECT RAISE(ABORT, '{message}'); END"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER config_versions_no_change ON config_versions")
        op.execute("DROP FUNCTION config_versions_append_only()")
    for table in (
        "config_versions",
        "admin_sessions",
        "admin_user_clients",
        "admin_users",
        "client_sessions",
        "client_profiles",
        "clients",
    ):
        op.drop_table(table)
