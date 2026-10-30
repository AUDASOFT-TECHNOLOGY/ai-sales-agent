"""Public pages and bank details written by the Admin; offline plan payment requests with receipts.

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_pages",
        sa.Column("page_key", sa.String(32), primary_key=True),
        sa.Column("locale", sa.String(8), primary_key=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
    )
    op.create_table(
        "plan_payment_requests",
        sa.Column("request_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), nullable=False),
        sa.Column("plan_id", sa.String(64), sa.ForeignKey("plans.plan_id"), nullable=False),
        sa.Column("plan_name", sa.String(80), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reference", sa.String(120), nullable=False, server_default=""),
        sa.Column("receipt_type", sa.String(32), nullable=False),
        sa.Column("receipt_size", sa.Integer, nullable=False),
        sa.Column("receipt_data", sa.LargeBinary, nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.String(255)),
        sa.Column("reason", sa.String(300)),
    )
    op.create_index(
        "ix_plan_payment_requests_client", "plan_payment_requests", ["client_id", "created_at"]
    )
    op.create_index(
        "uq_plan_payment_requests_pending",
        "plan_payment_requests",
        ["client_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("plan_payment_requests")
    op.drop_table("site_pages")
