"""The documents and links a client attached to its products, and their searchable passages.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_resources",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("clients.client_id"), nullable=False),
        sa.Column("product_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("url", sa.Text),
        sa.Column("filename", sa.String(200)),
        sa.Column("mime_type", sa.String(100)),
        sa.Column("size_bytes", sa.Integer),
        sa.Column("sha256", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_detail", sa.String(32)),
        sa.Column("text_chars", sa.Integer, nullable=False),
        sa.Column("content", sa.LargeBinary),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('link','file')", name="ck_product_resources_kind"),
        sa.CheckConstraint(
            "status IN ('ready','stored_only','failed')", name="ck_product_resources_status"
        ),
    )
    op.create_index(
        "ix_product_resources_product", "product_resources", ["client_id", "product_id"]
    )
    op.create_table(
        "product_resource_chunks",
        sa.Column(
            "resource_id",
            sa.String(64),
            sa.ForeignKey("product_resources.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer, primary_key=True),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("product_id", sa.String(64), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
    )
    op.create_index(
        "ix_product_resource_chunks_product",
        "product_resource_chunks",
        ["client_id", "product_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_product_resource_chunks_product", table_name="product_resource_chunks")
    op.drop_table("product_resource_chunks")
    op.drop_index("ix_product_resources_product", table_name="product_resources")
    op.drop_table("product_resources")
