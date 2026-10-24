"""Add private reusable embedding results without changing attachment identities."""

import sqlalchemy as sa
from alembic import op


revision = "0b7a16ef037b"
down_revision = "f9c053de926a"
branch_labels = None
depends_on = None


def _columns():
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "document_embedding_cache",
        *_columns(),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=True),
        sa.Column("lease_token", sa.String(32), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_document_embedding_cache_key", "document_embedding_cache", ["cache_key"], unique=True)
    op.create_table(
        "document_embedding_cache_reference",
        *_columns(),
        sa.Column("cache_id", sa.BigInteger(), sa.ForeignKey("document_embedding_cache.id"), nullable=False),
        sa.Column("use_token", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name, columns, unique in (
        ("use", ["cache_id", "use_token"], True),
        ("token", ["use_token"], False),
        ("expiry", ["expires_at"], False),
    ):
        op.create_index(
            "ix_document_embedding_cache_reference_" + name,
            "document_embedding_cache_reference",
            columns,
            unique=unique,
        )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM document_embedding_cache_reference LIMIT 1")).first():
        raise RuntimeError("Cannot discard referenced embedding results")
    op.drop_table("document_embedding_cache_reference")
    op.drop_table("document_embedding_cache")
