"""Where each mailbox that is read without being consumed was read up to.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "op_mail_cursor",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("mailbox_key", sa.String(64), primary_key=True),
        sa.Column("uid_validity", sa.BigInteger, nullable=False),
        sa.Column("next_uid", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("op_mail_cursor")
