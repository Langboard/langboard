"""Persist operator-selected employee membership for current card audience checks."""

import sqlalchemy as sa
from alembic import op


revision = "b6c7d8e9f0a1"
down_revision = "a5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "employee_membership_policy",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("group_uids", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("employee_membership_policy")
