"""Retain native card change receipts without inventing historical totals.

Quiesce every old writer before this migration and deploy receipt-aware writers
before resuming writes. The per-card boundary is the last legacy stamp, not the
number of missing events. Mixed-version writes cannot provide complete history.
"""

import sqlalchemy as sa
from alembic import op


revision = "c7921d41af65"
down_revision = "b6810c30fe54"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("card", sa.Column("change_count_since_seq", sa.BigInteger(), nullable=False, server_default="0"))
    op.execute("UPDATE card SET change_count_since_seq = last_change_seq")
    op.create_table(
        "card_change_receipt",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("card_id", sa.BigInteger(), sa.ForeignKey("card.id"), nullable=False),
        sa.Column("change_seq", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("card_id", "change_seq", name="uq_card_change_receipt_cursor"),
        sa.CheckConstraint("change_seq > 0", name="ck_card_change_receipt_positive"),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM card_change_receipt LIMIT 1")).first():
        raise RuntimeError("Cannot discard retained card change receipts")
    op.drop_table("card_change_receipt")
    op.drop_column("card", "change_count_since_seq")
