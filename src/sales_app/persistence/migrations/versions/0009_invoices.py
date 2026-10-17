"""Monthly invoices and their lines (a copy of the plan terms, so history never moves).

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("invoice_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True)),
        sa.Column("status_changed_by", sa.String(255)),
        sa.UniqueConstraint("client_id", "period", name="uq_invoices_client_period"),
    )
    op.create_index("ix_invoices_period", "invoices", ["period", "client_id"])
    op.create_table(
        "invoice_lines",
        sa.Column("line_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("invoice_id", sa.Integer, sa.ForeignKey("invoices.invoice_id"), nullable=False),
        sa.Column("plan_id", sa.String(64), nullable=False),
        sa.Column("plan_name", sa.String(80), nullable=False),
        sa.Column("monthly_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("included_credits", sa.Integer, nullable=False),
        sa.Column("daily_quota", sa.Integer, nullable=False),
        sa.Column("days", sa.Integer, nullable=False),
        sa.Column("days_in_period", sa.Integer, nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
    )
    op.create_index("ix_invoice_lines_invoice", "invoice_lines", ["invoice_id"])


def downgrade() -> None:
    op.drop_table("invoice_lines")
    op.drop_table("invoices")
