"""Persist personal inbox disposition independently of read and delivery state."""

import sqlalchemy as sa
from alembic import op


revision = "b6ee24fc7021"
down_revision = "e37a3108a9f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_notification", sa.Column("inbox_state", sa.String(length=5), nullable=True))


def downgrade() -> None:
    op.drop_column("user_notification", "inbox_state")
