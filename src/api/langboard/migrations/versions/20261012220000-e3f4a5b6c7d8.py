"""Immutable app-reported execution start receipts."""

import sqlalchemy as sa
from alembic import op


revision = "e3f4a5b6c7d8"
down_revision = "d2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_execution_start",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("lease_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("acknowledgment_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("runtime_reference", sa.String(), nullable=False),
        sa.Column("execution_reference", sa.String(), nullable=False),
    )


def downgrade():
    op.drop_table("app_execution_start")
