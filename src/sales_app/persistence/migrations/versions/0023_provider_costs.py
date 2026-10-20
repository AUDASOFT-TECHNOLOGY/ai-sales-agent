"""Technical costs by provider: the Admin's cost parameters and the cost lines.

Additive only. `cost_rates` holds unit prices the Admin provides (none by default: a missing price
means the cost is unavailable, never a guess). `provider_costs` holds cost lines with how they are
known (measured / estimated / unavailable).

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

_UTC = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "cost_rates",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("rate_key", sa.String(64), primary_key=True),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("updated_at", _UTC, nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
    )
    op.create_table(
        "provider_costs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(64), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("period_date", sa.Date, nullable=False),
        sa.Column("period_kind", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 6)),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 6)),
        sa.Column("amount", sa.Numeric(18, 6)),
        sa.Column("currency", sa.String(3)),
        sa.Column("basis", sa.String(16), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("recorded_at", _UTC, nullable=False),
        sa.Column("recorded_by", sa.String(255), nullable=False),
    )
    op.create_index("ix_provider_costs_period", "provider_costs", ["period_date", "provider"])
    op.create_index("ix_provider_costs_client", "provider_costs", ["client_id", "period_date"])


def downgrade() -> None:
    op.drop_index("ix_provider_costs_client", table_name="provider_costs")
    op.drop_index("ix_provider_costs_period", table_name="provider_costs")
    op.drop_table("provider_costs")
    op.drop_table("cost_rates")
