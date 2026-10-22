"""Add private physical attachment ownership without changing legacy locators."""

import sqlalchemy as sa
from alembic import op


revision = "a570fb29ed43"
down_revision = "9460fa18ec32"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "attachment_storage_object",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("storage_type", sa.String(32), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("allocation_token", sa.String(32), nullable=False),
        sa.Column("ready", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "uq_attachment_storage_content", "attachment_storage_object", ["storage_type", "content_sha256", "size_bytes"], unique=True
    )
    op.create_table(
        "attachment_storage_reference",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("object_id", sa.BigInteger(), sa.ForeignKey("attachment_storage_object.id"), nullable=False),
        sa.Column("storage_type", sa.String(32), nullable=False),
        sa.Column("storage_name", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("released", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "uq_attachment_storage_locator", "attachment_storage_reference", ["storage_type", "storage_name", "filename"], unique=True
    )
    op.create_index("ix_attachment_storage_reference_object", "attachment_storage_reference", ["object_id"])


def downgrade():
    connection = op.get_bind()
    for name in ("attachment_storage_reference", "attachment_storage_object"):
        if connection.execute(sa.text(f"SELECT 1 FROM {name} LIMIT 1")).first():
            raise RuntimeError("Cannot discard retained attachment storage ownership")
    op.drop_table("attachment_storage_reference")
    op.drop_table("attachment_storage_object")
