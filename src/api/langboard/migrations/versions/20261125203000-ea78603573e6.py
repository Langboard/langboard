"""Store each user's browser reminder preferences."""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from langboard_shared.core.db.ColumnTypes import SnowflakeIDType


revision: str = "ea78603573e6"
down_revision: str = "274d607ac378"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_reminder_web_push_preference",
        sa.Column("id", SnowflakeIDType, autoincrement=False, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", SnowflakeIDType, nullable=False),
        sa.Column("quiet_hours", sa.JSON(), nullable=True),
        sa.Column("block_urgent", sa.Boolean(), nullable=False),
        sa.Column("show_preview", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_reminder_web_push_preference")
