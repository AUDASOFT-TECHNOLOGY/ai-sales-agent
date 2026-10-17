"""The LLM usage register: one row per model call a client's agent made, and how it ended.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "op_llm_usage",
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("status", sa.String(16), nullable=False),
    )
    op.create_index("ix_op_llm_usage_client", "op_llm_usage", ["client_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("op_llm_usage")
