"""A client's WhatsApp account as a relation: Business Portfolio, WABA and number.

Additive: one new table, nothing existing changes and no secret is read or removed. The accounts
connected before this revision are adopted when the application starts (their ids are read from the
credential store and written here with `ownership = 'legacy_unverified'` and no Portfolio): until
their owner is verified by a new Embedded Signup connection they keep working.

Unique constraints: one Portfolio, one WABA and one phone number per client (NULLs excepted).

Revision ID: 0032
Revises: 0031
"""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wa_accounts",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("business_id", sa.String(32), nullable=True),
        sa.Column("waba_id", sa.String(32), nullable=True),
        sa.Column("phone_id", sa.String(32), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("ownership", sa.String(24), nullable=False),
        sa.Column("business_name", sa.String(200), nullable=False),
        sa.Column("verified_name", sa.String(200), nullable=False),
        sa.Column("display_number", sa.String(40), nullable=False),
        sa.Column("billing_state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("business_id", name="uq_wa_accounts_business"),
        sa.UniqueConstraint("waba_id", name="uq_wa_accounts_waba"),
        sa.UniqueConstraint("phone_id", name="uq_wa_accounts_phone"),
    )


def downgrade() -> None:
    op.drop_table("wa_accounts")
