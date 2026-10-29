"""What each client's agent is doing right now: one row per step, kept for two days.

Additive only: one new table. Steps hold a kind, a person's display name and times, never a message.

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "op_agent_activity",
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prospect_id", sa.String(255), nullable=True),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column("detail", sa.String(200), nullable=True),
    )
    op.create_index("ix_op_agent_activity_client", "op_agent_activity", ["client_id", "seq"])
    op.create_index(
        "ix_op_agent_activity_started", "op_agent_activity", ["client_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_table("op_agent_activity")
