"""Who is in charge of each conversation: the agent, or a person who took over.

Additive only: one new table, nothing existing changes. No row means the agent has always been in
charge, so applying this changes no behaviour until a conversation is handed over or taken.

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_control",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("prospect_id", sa.String(255), primary_key=True),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("taken_by", sa.String(128), nullable=True),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('agent', 'human')", name="ck_conversation_control_mode"),
    )
    op.create_index("ix_conversation_control_mode", "conversation_control", ["client_id", "mode"])


def downgrade() -> None:
    op.drop_index("ix_conversation_control_mode", table_name="conversation_control")
    op.drop_table("conversation_control")
