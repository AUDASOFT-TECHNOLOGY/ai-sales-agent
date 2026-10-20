"""Invoices: a 100% discount, distinct from FOR_FREE and from PAID.

Additive: five columns on `invoices`. Existing rows keep `discount_percent` 0 and the other columns
NULL, which reads as "no discount"; their `amount` (the billed amount) and statuses are untouched.
The audit of who and when is the existing `status_changed_by`/`status_changed_at` and the traced
`payment_events` row.

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoices") as batch:
        batch.add_column(
            sa.Column("discount_percent", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("amount_after_discount", sa.Numeric(18, 2), nullable=True))
        batch.add_column(sa.Column("discount_amount", sa.Numeric(18, 2), nullable=True))
        batch.add_column(sa.Column("free_amount", sa.Numeric(18, 2), nullable=True))
        batch.add_column(sa.Column("discount_reason", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("invoices") as batch:
        batch.drop_column("discount_reason")
        batch.drop_column("free_amount")
        batch.drop_column("discount_amount")
        batch.drop_column("amount_after_discount")
        batch.drop_column("discount_percent")
