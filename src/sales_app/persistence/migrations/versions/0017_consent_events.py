"""The consent registry's events in the database: append-only, hash-chained, one log per client.

Nothing existing changes: a deployment keeps its consent files until `sales-agent consent
import-log` is run for each client and `SALES_AGENT_CONSENT_STORE=sql` is set. The rows can never be
changed or removed once written (triggers), on top of the chain that makes any edit visible.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consent_events",
        sa.Column("client_id", sa.String(64), primary_key=True),
        sa.Column("seq", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.String(32), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(24), nullable=False),
        sa.Column("at", sa.String(40), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("evidence_ref", sa.String(80), nullable=False),
        sa.Column("prev_mac", sa.String(64), nullable=False),
        sa.Column("mac", sa.String(64), nullable=False),
        sa.UniqueConstraint("client_id", "event_id", name="uq_consent_events_event"),
        sa.CheckConstraint("seq >= 1", name="ck_consent_events_seq"),
    )
    op.create_index("ix_consent_events_key", "consent_events", ["client_id", "key"])
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION consent_events_append_only() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'consent_events is append-only'; END; $$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER consent_events_no_change BEFORE UPDATE OR DELETE ON consent_events "
            "FOR EACH ROW EXECUTE FUNCTION consent_events_append_only()"
        )
    elif dialect == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER consent_events_no_{action.lower()} BEFORE {action} ON consent_events "
                "BEGIN SELECT RAISE(ABORT, 'consent_events is append-only'); END"
            )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER consent_events_no_change ON consent_events")
        op.execute("DROP FUNCTION consent_events_append_only()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER consent_events_no_update")
        op.execute("DROP TRIGGER consent_events_no_delete")
    op.drop_index("ix_consent_events_key", table_name="consent_events")
    op.drop_table("consent_events")
