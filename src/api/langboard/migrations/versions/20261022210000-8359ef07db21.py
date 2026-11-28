"""Private native wiki document processing state."""

import sqlalchemy as sa
from alembic import op


revision = "8359ef07db21"
down_revision = "7248de96ca10"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("project_wiki_attachment", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.create_table(
        "wiki_attachment_document",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("attachment_id", sa.BigInteger(), sa.ForeignKey("project_wiki_attachment.id"), nullable=False),
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
        sa.UniqueConstraint("attachment_id", name="uq_wiki_attachment_document_attachment"),
        sa.CheckConstraint("status IN ('pending', 'processing', 'indexed', 'failed')", name="ck_wiki_document_status"),
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM project_wiki_attachment WHERE content_sha256 IS NOT NULL LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Cannot discard authoritative wiki attachment hashes")
    if op.get_bind().execute(sa.text("SELECT 1 FROM wiki_attachment_document LIMIT 1")).first():
        raise RuntimeError("Cannot discard native wiki document state or owned vector references")
    op.drop_table("wiki_attachment_document")
    op.drop_column("project_wiki_attachment", "content_sha256")
