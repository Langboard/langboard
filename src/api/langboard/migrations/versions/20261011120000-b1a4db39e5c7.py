"""Keep native interaction movement separate from answer and runtime receipts."""

import sqlalchemy as sa
from alembic import op


revision = "b1a4db39e5c7"
down_revision = "a093ca28d4b6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_card_interaction_transition",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("expected_card_revision", sa.BigInteger(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM app_card_interaction_transition LIMIT 1")).first():
        raise RuntimeError("Cannot discard interaction transition history")
    op.drop_table("app_card_interaction_transition")
