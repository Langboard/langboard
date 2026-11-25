"""Store per-occurrence reminder exceptions."""

import sqlalchemy as sa
from alembic import op


revision = "35e18ab705df"
down_revision = "38cd651acf07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("card_reminder_rule", sa.Column("next_regular_due_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE card_reminder_rule SET next_regular_due_at = next_due_at")
    op.add_column("card_reminder_occurrence", sa.Column("moved_to", sa.DateTime(timezone=True), nullable=True))
    op.add_column("card_reminder_occurrence", sa.Column("moved_processed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "card_reminder_occurrence",
        sa.Column("skipped", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    predicate = sa.text("moved_to IS NOT NULL AND moved_processed_at IS NULL AND skipped IS FALSE")
    op.create_index(
        "ix_card_reminder_occurrence_pending_move", "card_reminder_occurrence", ["rule_id", "moved_to", "id"],
        postgresql_where=predicate, sqlite_where=predicate,
    )


def downgrade() -> None:
    op.drop_index("ix_card_reminder_occurrence_pending_move", table_name="card_reminder_occurrence")
    op.drop_column("card_reminder_occurrence", "skipped")
    op.drop_column("card_reminder_occurrence", "moved_processed_at")
    op.drop_column("card_reminder_occurrence", "moved_to")
    op.drop_column("card_reminder_rule", "next_regular_due_at")
