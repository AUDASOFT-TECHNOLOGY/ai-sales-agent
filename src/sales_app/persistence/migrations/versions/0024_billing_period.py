"""Plans: the billing period (monthly today, annual prepared).

Additive: one column on `plans`. Existing plans read as "monthly", which is what they have always
been. Nothing else changes: the invoices keep their unique key on (client, period), which already
refuses a second invoice for the same client and period whatever the period's length.

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("plans") as batch:
        batch.add_column(
            sa.Column("billing_period", sa.String(8), nullable=False, server_default="monthly")
        )


def downgrade() -> None:
    with op.batch_alter_table("plans") as batch:
        batch.drop_column("billing_period")
