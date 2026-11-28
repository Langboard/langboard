"""Add native attachment content identity and historical object provenance."""

import sqlalchemy as sa
from alembic import op


revision = "2d03e9417c65"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("card_attachment", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.add_column("card_attachment", sa.Column("replaced_by_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_card_attachment_card_content_sha256", "card_attachment", ["card_id", "content_sha256"])
    op.create_index("ix_card_attachment_replaced_by_id", "card_attachment", ["replaced_by_id"])
    dialect = op.get_bind().dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        return  # Portable identity/card indices remain available on other native engines.
    keys = ("storage_type", "storage_name", "filename")
    expressions = (
        [f"((file::jsonb #>> ARRAY[]::text[])::jsonb ->> '{key}')" for key in keys]
        if dialect == "postgresql"
        else [f"json_extract(json_extract(file, '$'), '$.{key}')" for key in keys]
    )
    op.execute(
        "CREATE INDEX ix_card_attachment_historical_file_identity ON card_attachment ("
        + ", ".join(expressions)
        + ") WHERE deleted_at IS NOT NULL AND replaced_by_id IS NOT NULL"
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM card_attachment WHERE replaced_by_id IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Cannot discard historical attachment URL provenance")
    if op.get_bind().dialect.name in {"sqlite", "postgresql"}:
        op.drop_index("ix_card_attachment_historical_file_identity", table_name="card_attachment")
    op.drop_index("ix_card_attachment_replaced_by_id", table_name="card_attachment")
    op.drop_index("ix_card_attachment_card_content_sha256", table_name="card_attachment")
    op.drop_column("card_attachment", "replaced_by_id")
    op.drop_column("card_attachment", "content_sha256")
