"""Retain transactional vector cleanup requests after source deletion/replacement."""

import sqlalchemy as sa
from alembic import op


revision = "a31f9e7b402c"
down_revision = "f2098cef3604"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_vector_cleanup",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pointer", sa.JSON(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claim_token", sa.String(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
    )
    op.create_index("ix_document_vector_cleanup_due_at", "document_vector_cleanup", ["due_at"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM document_vector_cleanup LIMIT 1")).first():
        raise RuntimeError("Cannot discard pending document vector cleanup requests")
    op.drop_index("ix_document_vector_cleanup_due_at", table_name="document_vector_cleanup")
    op.drop_table("document_vector_cleanup")
