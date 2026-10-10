"""map projects to organizations

Revision ID: c8d2e4f17a90
Revises: b3f1a9c05e72
Create Date: 2026-09-19 07:45:00.000000

"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op
from langboard_shared.core.db.ColumnTypes import SnowflakeIDType


# revision identifiers, used by Alembic.
revision: str = "c8d2e4f17a90"
down_revision: Union[str, None] = "b3f1a9c05e72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("project")}


def upgrade() -> None:
    if "organization_id" in _column_names():
        return
    # Batch mode rebuilds SQLite tables and uses ordinary ALTER on PostgreSQL.
    with op.batch_alter_table("project") as batch:
        batch.add_column(sa.Column("organization_id", SnowflakeIDType, nullable=True))
        batch.create_foreign_key(
            op.f("fk_project_organization_id_organization"),
            "organization",
            ["organization_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(op.f("ix_project_organization_id"), ["organization_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("project") as batch:
        batch.drop_constraint(op.f("fk_project_organization_id_organization"), type_="foreignkey")
        batch.drop_index(op.f("ix_project_organization_id"))
        batch.drop_column("organization_id")
