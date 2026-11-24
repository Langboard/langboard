"""Native optional keyword connections and honest workload secret audit identity."""

import sqlalchemy as sa
from alembic import op


revision = "e06f74c10832"
down_revision = "d95e63b0f721"
branch_labels = None
depends_on = None


def upgrade():
    def common():
        return [
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        ]

    op.create_table(
        "keyword_search_connection",
        *common(),
        sa.Column("endpoint", sa.String(), nullable=False),
        sa.Column("index", sa.String(), nullable=False),
        sa.Column("secret_ref", sa.String(), nullable=False),
        sa.Column("secret_revision", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "keyword_search_configuration",
        *common(),
        sa.Column("singleton", sa.String(), nullable=False, unique=True),
        sa.Column("connection_id", sa.BigInteger(), nullable=True),
        sa.Column("reads_enabled", sa.Boolean(), nullable=False),
        sa.Column("indexing_enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.add_column("document_keyword_index", sa.Column("connection_id", sa.BigInteger(), nullable=True))

    op.create_index("ix_document_keyword_index_connection_id", "document_keyword_index", ["connection_id"])


def downgrade():
    bind = op.get_bind()
    if (
        bind.execute(sa.text("SELECT 1 FROM keyword_search_configuration LIMIT 1")).first()
        or bind.execute(sa.text("SELECT 1 FROM keyword_search_connection LIMIT 1")).first()
        or bind.execute(sa.text("SELECT 1 FROM document_keyword_index WHERE connection_id IS NOT NULL LIMIT 1")).first()
    ):
        raise RuntimeError("Cannot discard native keyword grants or workload audit history")
    op.drop_index("ix_document_keyword_index_connection_id", table_name="document_keyword_index")
    op.drop_column("document_keyword_index", "connection_id")
    op.drop_table("keyword_search_configuration")
    op.drop_table("keyword_search_connection")
