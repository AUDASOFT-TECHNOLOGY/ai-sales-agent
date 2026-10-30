"""Free accounts: the one-time free credits of a client who signed up by itself.

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_free_accounts",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("credits", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("client_free_accounts")
