"""Retain immutable human answers without implicit workflow or runtime effects."""

import sqlalchemy as sa
from alembic import op


revision = "fe82b917c3a5"
down_revision = "ed71a806b2f4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_card_interaction_response",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interaction_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("execution_generation", sa.Integer(), nullable=True),
        sa.Column("values", sa.JSON(), nullable=False),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM app_card_interaction_response LIMIT 1")).first():
        raise RuntimeError("Cannot discard human response history")
    op.drop_table("app_card_interaction_response")
