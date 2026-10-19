"""WhatsApp registers: the 24-hour windows, the delivery status of each message, the templates.

Nothing existing changes. Counts, ids and dates only: no message content.

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wa_contact_windows",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("wa_id", sa.String(32), primary_key=True),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "wa_deliveries",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("message_id", sa.String(128), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipient", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(16), nullable=False),
        sa.Column("error_title", sa.String(200), nullable=False),
    )
    op.create_index("ix_wa_deliveries_status", "wa_deliveries", ["client_id", "status", "at"])
    op.create_table(
        "wa_templates",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("name", sa.String(512), primary_key=True),
        sa.Column("language", sa.String(16), primary_key=True),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("parameter_names", sa.String(512), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("meta_id", sa.String(40), nullable=False),
        sa.Column("is_opening", sa.Boolean, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("wa_templates")
    op.drop_index("ix_wa_deliveries_status", table_name="wa_deliveries")
    op.drop_table("wa_deliveries")
    op.drop_table("wa_contact_windows")
