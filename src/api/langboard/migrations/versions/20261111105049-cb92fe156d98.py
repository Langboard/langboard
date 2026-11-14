"""Explicit per-user card activity notification subscriptions.

Generated with db:migrate (autogenerate=True) from de2ad7f6e055 on PostgreSQL18.6.
Only the generated subscription table and its index are retained; unrelated
existing schema drift is excluded.

Revision ID: cb92fe156d98
Revises: de2ad7f6e055
Create Date: 2026-11-11 10:50:49.857097

"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op
from langboard_shared.core.db.ColumnTypes import SnowflakeIDType


revision: str = "cb92fe156d98"
down_revision: Union[str, None] = "de2ad7f6e055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_card_notification_preference",
        sa.Column("id", SnowflakeIDType, autoincrement=False, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", SnowflakeIDType, nullable=False),
        sa.Column("card_id", SnowflakeIDType, nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.CheckConstraint(
            "mode IN ('ignore','comments','comments_and_moves')", name=op.f("ck_card_notification_mode")
        ),
        sa.ForeignKeyConstraint(
            ["card_id"], ["card.id"], name=op.f("fk_user_card_notification_preference_card_id_card"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_user_card_notification_preference_user_id_user"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_card_notification_preference")),
        sa.UniqueConstraint("user_id", "card_id", name="uq_user_card_notification_preference_subscription"),
    )
    op.create_index(
        op.f("ix_user_card_notification_preference_card_id"),
        "user_card_notification_preference",
        ["card_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_card_notification_preference_card_id"), table_name="user_card_notification_preference")
    op.drop_table("user_card_notification_preference")
