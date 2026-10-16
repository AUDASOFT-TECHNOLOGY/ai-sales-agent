"""The write-back ledger: what Revansa tried to write into a client's source, and how each try ended.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "op_writeback_ledger",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("sequence", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("attempt_id", sa.String(128), nullable=False),
        sa.Column("prospect_id", sa.String(255), nullable=False),
        sa.Column("field", sa.String(32), nullable=False),
        sa.Column("new_value", sa.String(255), nullable=False),
        sa.Column("expected_value", sa.String(255), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("code", sa.String(16)),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("client_id", "attempt_id", name="uq_op_writeback_ledger_attempt"),
    )
    op.create_index(
        "ix_op_writeback_ledger_prospect",
        "op_writeback_ledger",
        ["client_id", "prospect_id", "field", "sequence"],
    )


def downgrade() -> None:
    op.drop_table("op_writeback_ledger")
