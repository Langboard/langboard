"""Account and board scoped favorite filter conditions."""

import sqlalchemy as sa
from alembic import op


revision = "b392a7f43165"
down_revision = "a28196e32054"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_board_filter_preference",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("pinned_filters", sa.JSON(), nullable=False),
        sa.UniqueConstraint("user_id", "project_id", name="user_board_filter_preference_scope"),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM user_board_filter_preference LIMIT 1")).first():
        raise RuntimeError("Cannot discard saved filter preferences")
    op.drop_table("user_board_filter_preference")
