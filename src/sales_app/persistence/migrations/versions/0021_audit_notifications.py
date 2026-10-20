"""The central audit log and the notifications by role.

Nothing existing changes. The audit keeps redacted before/after documents (never a secret); the
notifications keep fixed sentences with a suggested action.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=True),
        sa.Column("action", sa.String(96), nullable=False),
        sa.Column("target", sa.String(256), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("before", json_type, nullable=True),
        sa.Column("after", json_type, nullable=True),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"])
    op.create_index("ix_audit_log_client", "audit_log", ["client_id", "at"])
    op.create_index("ix_audit_log_action", "audit_log", ["action", "at"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("audience", sa.String(8), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("message", sa.String(400), nullable=False),
        sa.Column("suggested_action", sa.String(300), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("dedupe_key", name="uq_notifications_dedupe"),
    )
    op.create_index(
        "ix_notifications_audience", "notifications", ["audience", "client_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_audience", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_audit_log_client", table_name="audit_log")
    op.drop_index("ix_audit_log_at", table_name="audit_log")
    op.drop_table("audit_log")
