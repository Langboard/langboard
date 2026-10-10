"""Retain app-card requests independently of agent runtimes."""

import sqlalchemy as sa
from alembic import op


revision = "ed71a806b2f4"
down_revision = "dc60b3745ae2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_card_interaction",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("card_id", sa.BigInteger(), nullable=False),
        sa.Column("connection_id", sa.BigInteger(), nullable=False),
        sa.Column("app_key", sa.String(), nullable=False),
        sa.Column("app_request_key", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("connection_id", "card_id", "app_request_key", name="uq_app_card_interaction_key"),
        sa.CheckConstraint("state IN ('pending','expired','cancelled')", name="ck_app_card_interaction_state"),
    )
    op.create_index("ix_app_card_interaction_card_id", "app_card_interaction", ["card_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM app_card_interaction LIMIT 1")).first():
        raise RuntimeError("Cannot discard app interaction history")
    op.drop_table("app_card_interaction")
