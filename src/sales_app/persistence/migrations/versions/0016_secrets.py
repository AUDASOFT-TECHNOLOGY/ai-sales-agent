"""Credentials in the database, encrypted: the store every client's integrations are kept in.

Only ciphertext is stored; the master key stays in the deployment's environment. Nothing existing
changes: a deployment keeps its credential file until `sales-agent secrets migrate-to-db`.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "secrets",
        sa.Column("scope", sa.String(128), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
    )
    op.create_index("ix_secrets_key", "secrets", ["key"])
    op.create_table(
        "secret_audit",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(128), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32)),
    )


def downgrade() -> None:
    op.drop_table("secret_audit")
    op.drop_index("ix_secrets_key", table_name="secrets")
    op.drop_table("secrets")
