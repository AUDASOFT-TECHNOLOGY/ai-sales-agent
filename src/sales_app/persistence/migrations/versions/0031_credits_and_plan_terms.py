"""Daily credits on plans, the credit ledger columns, the credit counters and holds.

Additive and safe to run on a live database:

- `plans` and `client_plan_assignments` get `daily_credits`. Existing rows are given their monthly
  credits as daily credits (no daily limit stricter than the monthly one) until the Admin sets it.
- The three reference plans (Starter, Business, Enterprise), when they exist under those names, are
  brought to the reference terms. Any other plan keeps its own.
- `op_llm_usage` gets the columns of the credit ledger (call id, operation, exact cost, credits). The
  calls already recorded keep NULL there: they are not priced after the fact.
- `op_credit_counters` and `op_credit_holds` start empty: consumption is counted from now on.

Revision ID: 0031
Revises: 0030
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

# name_key -> (monthly price in USD, monthly credits, daily credits, daily commercial action quota)
_REFERENCE = {
    "starter": ("59.00", 25_000, 800, 10),
    "business": ("149.00", 75_000, 2_500, 30),
    "enterprise": ("399.00", 200_000, 6_600, 100),
}


def upgrade() -> None:
    for table in ("plans", "client_plan_assignments"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("daily_credits", sa.Integer, nullable=False, server_default="0")
            )
        op.execute(sa.text(f"UPDATE {table} SET daily_credits = included_credits"))
    plans = sa.table(
        "plans",
        sa.column("name_key", sa.String),
        sa.column("monthly_price", sa.Numeric),
        sa.column("currency", sa.String),
        sa.column("included_credits", sa.Integer),
        sa.column("daily_credits", sa.Integer),
        sa.column("daily_quota", sa.Integer),
    )
    for key, (price, monthly, daily, quota) in _REFERENCE.items():
        op.execute(
            plans.update()
            .where(plans.c.name_key == key)
            .values(
                monthly_price=sa.literal(price, sa.Numeric(18, 2)),
                currency="USD",
                included_credits=monthly,
                daily_credits=daily,
                daily_quota=quota,
            )
        )
    with op.batch_alter_table("op_llm_usage") as batch:
        batch.add_column(sa.Column("call_id", sa.String(96)))
        batch.add_column(sa.Column("operation", sa.String(32)))
        batch.add_column(sa.Column("cost_pico_usd", sa.BigInteger))
        batch.add_column(sa.Column("credits_nano", sa.BigInteger))
    op.create_index("uq_op_llm_usage_call", "op_llm_usage", ["client_id", "call_id"], unique=True)
    op.create_table(
        "op_credit_counters",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("period", sa.String(16), primary_key=True),
        sa.Column("consumed_nano", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("held_nano", sa.BigInteger, nullable=False, server_default="0"),
    )
    op.create_table(
        "op_credit_holds",
        sa.Column("hold_id", sa.String(96), primary_key=True),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), nullable=False),
        sa.Column("periods", sa.String(64), nullable=False),
        sa.Column("amount_nano", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_op_credit_holds_client", "op_credit_holds", ["client_id", "created_at"])


def downgrade() -> None:
    op.drop_table("op_credit_holds")
    op.drop_table("op_credit_counters")
    op.drop_index("uq_op_llm_usage_call", table_name="op_llm_usage")
    with op.batch_alter_table("op_llm_usage") as batch:
        for column in ("credits_nano", "cost_pico_usd", "operation", "call_id"):
            batch.drop_column(column)
    for table in ("client_plan_assignments", "plans"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("daily_credits")
