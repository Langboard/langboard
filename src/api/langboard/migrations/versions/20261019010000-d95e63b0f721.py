"""Retain desired keyword projections and deletion fences outside attachment rows."""

import sqlalchemy as sa
from alembic import op


revision = "d95e63b0f721"
down_revision = "c84d52a9e610"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_keyword_index",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attachment_id", sa.BigInteger(), nullable=False),
        sa.Column("card_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("destination_fingerprint", sa.String(), nullable=False),
        sa.Column("destination", sa.JSON(), nullable=False),
        sa.Column("source_generation", sa.String(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("completed_version", sa.BigInteger(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.String(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.UniqueConstraint("attachment_id", "destination_fingerprint", name="uq_document_keyword_index_destination"),
        sa.CheckConstraint(
            "version >= 1 AND completed_version >= 0 AND completed_version <= version",
            name=op.f("ck_document_keyword_index_versions"),
        ),
        sa.CheckConstraint("operation IN ('index', 'remove')", name=op.f("ck_document_keyword_index_operation")),
    )
    op.create_index(
        "ix_document_keyword_index_scope",
        "document_keyword_index",
        ["project_id", "card_id"],
    )
    op.create_index(
        "ix_document_keyword_index_due_at",
        "document_keyword_index",
        ["due_at"],
        postgresql_where=sa.text("due_at IS NOT NULL"),
        sqlite_where=sa.text("due_at IS NOT NULL"),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM document_keyword_index LIMIT 1")).first():
        raise RuntimeError("Cannot discard keyword index ownership or pending cleanup")
    op.drop_index("ix_document_keyword_index_due_at", table_name="document_keyword_index")
    op.drop_index("ix_document_keyword_index_scope", table_name="document_keyword_index")
    op.drop_table("document_keyword_index")
