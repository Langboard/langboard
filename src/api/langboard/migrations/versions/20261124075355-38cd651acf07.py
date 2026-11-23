"""store card reminder schedules

Revision ID: 38cd651acf07
Revises: b6ee24fc7021
Create Date: 2026-11-24 07:53:55.046398

"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op
from langboard_shared.core.db.ColumnTypes import SnowflakeIDType


# revision identifiers, used by Alembic.
revision: str = "38cd651acf07"
down_revision: Union[str, None] = "b6ee24fc7021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "card_reminder_rule",
        sa.Column("id", SnowflakeIDType, autoincrement=False, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("card_id", SnowflakeIDType, nullable=False),
        sa.Column("owner_id", SnowflakeIDType, nullable=False),
        sa.Column("is_shared", sa.Boolean(), nullable=False),
        sa.Column("schedule", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("request_uid", sa.String(length=36), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("creation_receipt", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("consumed_count", sa.Integer(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_snapshot", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("revision > 0 AND consumed_count >= 0", name=op.f("ck_card_reminder_rule_counters")),
        sa.ForeignKeyConstraint(["card_id"], ["card.id"], name=op.f("fk_card_reminder_rule_card_id_card")),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], name=op.f("fk_card_reminder_rule_owner_id_user")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_card_reminder_rule")),
        sa.UniqueConstraint("owner_id", "request_uid", name="uq_card_reminder_rule_request"),
    )
    op.create_index(op.f("ix_card_reminder_rule_card_id"), "card_reminder_rule", ["card_id"], unique=False)
    op.create_index("ix_card_reminder_rule_due", "card_reminder_rule", ["next_due_at"], unique=False)
    op.create_index(op.f("ix_card_reminder_rule_owner_id"), "card_reminder_rule", ["owner_id"], unique=False)
    op.create_table(
        "card_reminder_occurrence",
        sa.Column("id", SnowflakeIDType, autoincrement=False, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("rule_id", SnowflakeIDType, nullable=False),
        sa.Column("rule_revision", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["card_reminder_rule.id"], name=op.f("fk_card_reminder_occurrence_rule_id_card_reminder_rule")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_card_reminder_occurrence")),
        sa.UniqueConstraint("rule_id", "due_at", name="uq_card_reminder_occurrence_due"),
    )


def downgrade() -> None:
    op.drop_table("card_reminder_occurrence")
    op.drop_index(op.f("ix_card_reminder_rule_owner_id"), table_name="card_reminder_rule")
    op.drop_index("ix_card_reminder_rule_due", table_name="card_reminder_rule")
    op.drop_index(op.f("ix_card_reminder_rule_card_id"), table_name="card_reminder_rule")
    op.drop_table("card_reminder_rule")
