"""Payment confirmation: how an invoice was paid, and the trace of every payment decision.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("payment_origin", sa.String(16)))
    op.add_column("invoices", sa.Column("payment_transaction_id", sa.String(128)))
    op.add_column("invoices", sa.Column("payment_event_id", sa.String(128)))
    op.create_index("uq_invoices_transaction", "invoices", ["payment_transaction_id"], unique=True)
    # Invoices already PAID were declared by an Admin: say so.
    op.execute("UPDATE invoices SET payment_origin = 'manual' WHERE status = 'PAID'")
    op.create_table(
        "payment_events",
        sa.Column("event_id", sa.String(128), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("invoice_id", sa.Integer, sa.ForeignKey("invoices.invoice_id")),
        sa.Column("client_id", sa.String(64)),
        sa.Column("invoice_reference", sa.String(100)),
        sa.Column("amount", sa.Numeric(18, 2)),
        sa.Column("currency", sa.String(3)),
        sa.Column("transaction_id", sa.String(128)),
        sa.Column("event_status", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_payment_events_invoice", "payment_events", ["invoice_id"])


def downgrade() -> None:
    op.drop_table("payment_events")
    op.drop_index("uq_invoices_transaction", table_name="invoices")
    with op.batch_alter_table("invoices") as batch:
        batch.drop_column("payment_event_id")
        batch.drop_column("payment_transaction_id")
        batch.drop_column("payment_origin")
