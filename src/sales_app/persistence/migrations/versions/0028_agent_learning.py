"""What an agent keeps to learn from its results: anonymous traces, verdicts, and the rules held.

Additive only: three new tables. Nothing here holds a name, a contact or a message: a person is an
opaque reference, a message a length, a rule a code. Traces are deleted once reviewed.

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "op_learning_traces",
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), nullable=False),
        sa.Column("case_ref", sa.String(32), nullable=False),
        sa.Column("action_ref", sa.String(32), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(16), nullable=True),
        sa.Column("length", sa.Integer, nullable=True),
        sa.Column("questions", sa.Integer, nullable=True),
        sa.Column("gap_hours", sa.Numeric(10, 1), nullable=True),
        sa.Column("unanswered", sa.Integer, nullable=False, server_default="0"),
        sa.Column("answers_reply", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("labels", sa.Text, nullable=False, server_default="{}"),
        sa.UniqueConstraint("client_id", "action_ref", name="uq_op_learning_traces_action"),
    )
    op.create_index("ix_op_learning_traces_case", "op_learning_traces", ["client_id", "case_ref"])
    op.create_table(
        "op_learning_reviews",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("case_ref", sa.String(32), primary_key=True),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("causes", sa.Text, nullable=False, server_default=""),
        sa.Column("practices", sa.Text, nullable=False, server_default=""),
        sa.Column("confidence", sa.String(8), nullable=False),
        sa.Column("turning_step", sa.Integer, nullable=True),
        sa.Column("unknown", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("product", sa.String(64), nullable=True),
        sa.Column("applied_notes", sa.Text, nullable=False, server_default=""),
        sa.Column("steps_seen", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_op_learning_reviews_ended", "op_learning_reviews", ["client_id", "ended_at"]
    )
    op.create_table(
        "op_learning_notes",
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), primary_key=True),
        sa.Column("note_id", sa.String(96), primary_key=True),
        sa.Column("code", sa.String(48), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("theme", sa.String(32), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("product", sa.String(64), nullable=True),
        sa.Column("evidence", sa.String(8), nullable=False),
        sa.Column("cases", sa.Integer, nullable=False, server_default="0"),
        sa.Column("against", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reason", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("off_by", sa.String(8), nullable=True),
        sa.Column("confirmed_by_client", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("op_learning_notes")
    op.drop_table("op_learning_reviews")
    op.drop_table("op_learning_traces")
