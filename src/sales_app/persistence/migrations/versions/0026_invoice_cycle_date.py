"""Invoices: the period becomes the cycle date (`YYYY-MM-DD`).

Additive and compatible: the `period` column is widened from 7 to 10 characters so it can hold a
cycle date. Existing invoices keep their `YYYY-MM` period (the former calendar-month rule) and stay
readable; their data is not touched. The unique key on (client, period) is unchanged. Invoices are
no longer prorated: a new line is the plan at its price (`days` and `days_in_period` stay, set to 1).

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def _resize(old: int, new: int) -> None:
    # SQLite does not enforce the length of a VARCHAR, and rebuilding `invoices` there would have
    # to drop a table that `invoice_lines` references: nothing to do. Other databases alter it.
    if op.get_bind().dialect.name == "sqlite":
        return
    op.alter_column(
        "invoices",
        "period",
        existing_type=sa.String(old),
        type_=sa.String(new),
        existing_nullable=False,
    )


def upgrade() -> None:
    _resize(7, 10)


def downgrade() -> None:
    # Only safe while no invoice carries a `YYYY-MM-DD` period.
    _resize(10, 7)
