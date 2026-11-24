"""Persist current and previous avatars with optimistic revisions."""

import sqlalchemy as sa
from alembic import op


revision = "a28196e32054"
down_revision = "f17085d21943"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("project", sa.Column("avatar", sa.JSON(), nullable=True))
    for table in ("project", "user"):
        op.add_column(table, sa.Column("previous_avatar", sa.JSON(), nullable=True))
        op.add_column(table, sa.Column("avatar_revision", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    for table in ("project", "user"):
        op.drop_column(table, "avatar_revision")
        op.drop_column(table, "previous_avatar")
    op.drop_column("project", "avatar")
