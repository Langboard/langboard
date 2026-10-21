"""Add explicit compactor board grants; existing boards receive no authority."""

import sqlalchemy as sa
from alembic import op


revision = "a3e607cf912b"
down_revision = "64c8c0a4baf1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "compactor_board_grant",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("internal_bot_id", sa.BigInteger(), nullable=False),
        sa.Column("granted_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("can_read", sa.Boolean(), nullable=False),
        sa.Column("can_compact", sa.Boolean(), nullable=False),
        sa.Column("can_archive", sa.Boolean(), nullable=False),
        sa.CheckConstraint("can_read OR (NOT can_compact AND NOT can_archive)", name="compactor_grant_requires_read"),
    )
    op.create_index("ix_compactor_board_grant_identity", "compactor_board_grant", ["project_id", "internal_bot_id"], unique=True)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM compactor_board_grant LIMIT 1")).first():
        raise RuntimeError("Cannot discard explicit compactor authority records")
    op.drop_table("compactor_board_grant")
