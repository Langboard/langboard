"""Persist the explicit reminder channel and its per-device delivery receipts."""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from langboard_shared.core.db.ColumnTypes import SnowflakeIDType


revision: str = "274d607ac378"
down_revision: str = "487ac0aea974"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "card_reminder_rule", sa.Column("web_push_delivery", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.create_table(
        "card_reminder_web_push_delivery",
        sa.Column("id", SnowflakeIDType, autoincrement=False, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("notification_id", SnowflakeIDType, nullable=False),
        sa.Column("subscription_id", SnowflakeIDType, nullable=False),
        sa.Column("endpoint_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_result", sa.String(32), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["user_notification.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subscription_id"], ["user_web_push_subscription.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id", "subscription_id", name="uq_reminder_web_push_recipient_device"),
    )
    op.create_index("ix_reminder_web_push_due", "card_reminder_web_push_delivery", ["state", "next_attempt_at"])


def downgrade() -> None:
    op.drop_table("card_reminder_web_push_delivery")
    op.drop_column("card_reminder_rule", "web_push_delivery")
