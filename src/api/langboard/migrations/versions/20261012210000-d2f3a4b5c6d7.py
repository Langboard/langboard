"""Retained runtime admission leases with hashed stop-only credentials."""

import sqlalchemy as sa
from alembic import op


revision = "d2f3a4b5c6d7"
down_revision = "c1f2a3b4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_execution_lease",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("request_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("acknowledgment_id", sa.BigInteger(), nullable=False),
        sa.Column("credential_id", sa.BigInteger(), nullable=True),
        sa.Column("runtime_token_hash", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="authorized"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("history", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade():
    op.drop_table("app_execution_lease")
