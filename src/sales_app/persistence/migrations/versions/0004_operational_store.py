"""A client's operational data: actions, messages, audit, facts, prospect states, local prospects.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _client() -> sa.Column:
    return sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), nullable=False)


def _document() -> sa.Column:
    return sa.Column(
        "document",
        sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "op_actions",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("action_id", sa.String(128), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        _document(),
        sa.UniqueConstraint("client_id", "idempotency_key", name="uq_op_actions_idempotency_key"),
    )
    op.create_index("ix_op_actions_status", "op_actions", ["client_id", "status"])

    op.create_table(
        "op_messages",
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        _client(),
        sa.Column("message_id", sa.String(128), nullable=False),
        sa.Column("prospect_id", sa.String(255), nullable=False),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        _document(),
        sa.UniqueConstraint("client_id", "message_id", name="uq_op_messages_message_id"),
    )
    op.create_index("ix_op_messages_prospect", "op_messages", ["client_id", "prospect_id"])

    op.create_table(
        "op_audit",
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        _client(),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("prospect_id", sa.String(255)),
        sa.Column("action_id", sa.String(128)),
        _document(),
    )
    op.create_index("ix_op_audit_prospect", "op_audit", ["client_id", "prospect_id"])

    op.create_table(
        "op_facts",
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        _client(),
        sa.Column("fact_id", sa.String(64), nullable=False),
        sa.Column("prospect_id", sa.String(255), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        _document(),
        sa.UniqueConstraint("client_id", "fact_id", name="uq_op_facts_fact_id"),
    )
    op.create_index("ix_op_facts_prospect", "op_facts", ["client_id", "prospect_id"])

    op.create_table(
        "op_prospect_states",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("prospect_id", sa.String(255), primary_key=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("conversation_id", sa.String(255)),
        _document(),
    )

    op.create_table(
        "op_local_prospects",
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        _client(),
        sa.Column("prospect_id", sa.String(255), nullable=False),
        _document(),
        sa.UniqueConstraint("client_id", "prospect_id", name="uq_op_local_prospects_prospect_id"),
    )

    op.create_table(
        "op_local_contacts",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("contact_key", sa.String(320), primary_key=True),
        sa.Column("prospect_id", sa.String(255), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "op_local_contacts",
        "op_local_prospects",
        "op_prospect_states",
        "op_facts",
        "op_audit",
        "op_messages",
        "op_actions",
    ):
        op.drop_table(table)
