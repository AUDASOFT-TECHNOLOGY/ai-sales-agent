"""A client's identity, status and engine in the database, no longer only in `client.yaml`.

Every column is nullable: NULL means "not migrated yet, the historical `client.yaml` decides", so
the migration changes no existing client's behaviour. `sales-agent db clients` fills them.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("display_name", sa.String(80)))
    op.add_column("clients", sa.Column("enabled", sa.Boolean))
    op.add_column("clients", sa.Column("engine", sa.String(64)))
    op.add_column("clients", sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.add_column("clients", sa.Column("updated_by", sa.String(255)))


def downgrade() -> None:
    with op.batch_alter_table("clients") as batch:
        for column in ("updated_by", "updated_at", "engine", "enabled", "display_name"):
            batch.drop_column(column)
