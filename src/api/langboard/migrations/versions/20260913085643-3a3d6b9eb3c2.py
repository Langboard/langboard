"""empty message

Revision ID: 3a3d6b9eb3c2
Revises: 97671190258e
Create Date: 2026-09-13 08:56:43.854311

"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op
from langboard_shared.core.db.ColumnTypes import SnowflakeIDType


# revision identifiers, used by Alembic.
revision: str = '3a3d6b9eb3c2'
down_revision: Union[str, None] = '97671190258e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Follow the existing batch migration pattern for SQLite constraint changes.
    # The previous SQLite implementation could add the column before failing
    # to add its constraint, without advancing the Alembic revision.
    bind = op.get_bind()
    has_column = bind.dialect.name == "sqlite" and any(
        column["name"] == "ai_chat_history_id" for column in sa.inspect(bind).get_columns("internal_bot_run")
    )
    with op.batch_alter_table("internal_bot_run") as batch:
        if not has_column:
            batch.add_column(sa.Column("ai_chat_history_id", SnowflakeIDType, nullable=True))
        batch.create_foreign_key(
            op.f("fk_internal_bot_run_ai_chat_history_id_chat_history"),
            "chat_history",
            ["ai_chat_history_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("internal_bot_run") as batch:
        batch.drop_constraint(op.f("fk_internal_bot_run_ai_chat_history_id_chat_history"), type_="foreignkey")
        batch.drop_column("ai_chat_history_id")
