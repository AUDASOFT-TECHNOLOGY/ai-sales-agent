"""The mandatory daily send cap of each client, and the count of what it sent each UTC day.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_send_caps",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("daily_cap", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.CheckConstraint("daily_cap >= 1", name="ck_client_send_caps_cap"),
    )
    op.create_table(
        "op_send_counter",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("channel", sa.String(32), primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("used", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("used >= 0", name="ck_op_send_counter_used"),
    )


def downgrade() -> None:
    op.drop_table("op_send_counter")
    op.drop_table("client_send_caps")
