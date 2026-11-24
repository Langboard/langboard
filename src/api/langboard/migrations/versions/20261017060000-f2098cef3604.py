"""Persist account-owned app rail preferences without changing app grants."""

import sqlalchemy as sa
from alembic import op


revision = "f2098cef3604"
down_revision = "e1987bdf2503"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_app_preference",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("pinned_apps", sa.JSON(), nullable=False),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM user_app_preference LIMIT 1")).first():
        raise RuntimeError("Cannot discard saved app rail preferences")
    op.drop_table("user_app_preference")
