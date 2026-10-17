"""The LLM price list: what a million input and output tokens cost, by provider and model.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_pricing",
        sa.Column("provider", sa.String(64), primary_key=True),
        sa.Column("model", sa.String(128), primary_key=True),
        sa.Column("input_per_million", sa.Numeric(18, 6), nullable=False),
        sa.Column("output_per_million", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("llm_pricing")
