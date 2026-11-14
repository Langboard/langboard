"""Private shared document extraction results and lifecycle references."""

import sqlalchemy as sa
from alembic import op


revision = "6137cd85bf09"
down_revision = "5026bc74af98"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_extraction_cache",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=True),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("document_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("lease_token", sa.String(32), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_document_extraction_cache_key", "document_extraction_cache", ["cache_key"], unique=True)
    op.create_table(
        "document_extraction_cache_reference",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("cache_id", sa.BigInteger(), sa.ForeignKey("document_extraction_cache.id"), nullable=False),
        sa.Column("use_token", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=True),
        sa.Column("source_uid", sa.String(128), nullable=True),
        sa.Column("generation", sa.String(32), nullable=True),
    )
    for name, columns, unique in (
        ("use", ["cache_id", "use_token"], True),
        ("token", ["use_token"], False),
        ("expiry", ["expires_at"], False),
        ("source", ["source_type", "source_uid"], False),
    ):
        op.create_index(
            f"ix_document_extraction_cache_reference_{name}",
            "document_extraction_cache_reference",
            columns,
            unique=unique,
        )


def downgrade():
    for table in ("document_extraction_cache_reference", "document_extraction_cache"):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("Cannot discard private extraction results or source references")
    op.drop_table("document_extraction_cache_reference")
    op.drop_table("document_extraction_cache")
