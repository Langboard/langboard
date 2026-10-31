"""Private native project document processing state."""

import sqlalchemy as sa
from alembic import op


revision = "5026bc74af98"
down_revision = "4f25ab639e87"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_attachment_document",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("attachment_id", sa.BigInteger(), sa.ForeignKey("project_attachment.id"), nullable=False),
        sa.Column("requested_by_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("generation", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("vision_config", sa.JSON(), nullable=False),
        sa.Column("embedding_config", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("document_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(128), nullable=True),
        sa.UniqueConstraint("attachment_id", name="uq_project_attachment_document_attachment"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'indexed', 'failed')", name="ck_project_document_status"
        ),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM project_attachment_document LIMIT 1")).first():
        raise RuntimeError("Cannot discard native project document state or owned vector references")
    op.drop_table("project_attachment_document")
