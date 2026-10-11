"""Retain app archive evidence independently of native cards and provider state."""

import sqlalchemy as sa
from alembic import op


revision = "c2b5ec40f6d8"
down_revision = "b1a4db39e5c7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_card_archive",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("card_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("project_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("app_key", sa.String(), nullable=False),
        sa.Column("card_revision", sa.BigInteger(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.UniqueConstraint("card_id", "card_revision", name="uq_app_card_archive_revision"),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM app_card_archive LIMIT 1")).first():
        raise RuntimeError("Cannot discard app archive history")
    op.drop_table("app_card_archive")
