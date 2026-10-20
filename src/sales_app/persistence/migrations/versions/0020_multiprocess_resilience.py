"""Multi-process resilience: pending OAuth consents and per-client job leases.

Additive: two new tables, nothing existing changes. No credential and no personal data: a pending
consent holds a session id, a client id, a short-lived PKCE verifier and an expiry; a lease holds a
client id, a job name, a holder id and two dates.

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_pending_consents",
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("session_id", sa.String(128), primary_key=True),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("verifier", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oauth_pending_expires", "oauth_pending_consents", ["expires_at"])
    op.create_table(
        "job_leases",
        sa.Column("client_id", sa.String(64), primary_key=True),
        sa.Column("job", sa.String(32), primary_key=True),
        sa.Column("holder", sa.String(64), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("job_leases")
    op.drop_index("ix_oauth_pending_expires", table_name="oauth_pending_consents")
    op.drop_table("oauth_pending_consents")
