"""Retain global source and display snapshots without changing existing local labels."""

import sqlalchemy as sa
from alembic import op
from langboard_shared.core.db.ColumnTypes import SnowflakeIDType


revision = "39ea4fd713c2"
down_revision = "28d93ec602b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("project_label") as batch:
        batch.add_column(sa.Column("global_label_id", SnowflakeIDType(), nullable=True))
        batch.add_column(sa.Column("global_display", sa.JSON(), nullable=True))
        batch.create_foreign_key("fk_project_label_global_source", "global_label", ["global_label_id"], ["id"])
        batch.create_unique_constraint("uq_project_label_global_source", ["project_id", "global_label_id"])


def downgrade() -> None:
    with op.batch_alter_table("project_label") as batch:
        batch.drop_constraint("uq_project_label_global_source", type_="unique")
        batch.drop_constraint("fk_project_label_global_source", type_="foreignkey")
        batch.drop_column("global_display")
        batch.drop_column("global_label_id")
