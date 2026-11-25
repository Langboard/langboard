"""Add automatic deadline reminder preference."""

import sqlalchemy as sa
from alembic import op


revision = "8c7b5792ee5e"
down_revision = "35e18ab705df"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("auto_deadline_reminder", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column("user", "auto_deadline_reminder")
