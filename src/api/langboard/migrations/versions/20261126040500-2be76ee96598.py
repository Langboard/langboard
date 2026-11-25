"""Retain actor-owned native card creation keys."""

import sqlalchemy as sa
from alembic import op


revision = "2be76ee96598"
down_revision = "7bde71b402af"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "card_creation_receipt",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("actor_type", sa.String(4), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("request_uuid", sa.String(36), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("card_id", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("actor_type", "actor_id", "request_uuid", name="uq_card_creation_request"),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM card_creation_receipt LIMIT 1")).first():
        raise RuntimeError("Cannot discard card creation retry receipts")
    op.drop_table("card_creation_receipt")
