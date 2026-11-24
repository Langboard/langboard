"""Store per-occurrence reminder skips."""

import sqlalchemy as sa
from alembic import op


revision = "35e18ab705df"
down_revision = "38cd651acf07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "card_reminder_occurrence",
        sa.Column("skipped", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("card_reminder_occurrence", "skipped")
