"""Commercial plans, and the plan each client is on (with the terms as they were at the time).

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("plan_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("name_key", sa.String(80), nullable=False, unique=True),
        sa.Column("monthly_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("included_credits", sa.Integer, nullable=False),
        sa.Column("daily_quota", sa.Integer, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
    )
    op.create_table(
        "client_plan_assignments",
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), nullable=False),
        sa.Column("plan_id", sa.String(64), sa.ForeignKey("plans.plan_id"), nullable=False),
        sa.Column("plan_name", sa.String(80), nullable=False),
        sa.Column("monthly_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("included_credits", sa.Integer, nullable=False),
        sa.Column("daily_quota", sa.Integer, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("assigned_by", sa.String(255), nullable=False),
    )
    op.create_index(
        "ix_client_plan_assignments_client", "client_plan_assignments", ["client_id", "started_at"]
    )
    # At most one current assignment per client, enforced by the database itself.
    op.create_index(
        "uq_client_plan_assignments_current",
        "client_plan_assignments",
        ["client_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
        sqlite_where=sa.text("ended_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("client_plan_assignments")
    op.drop_table("plans")
