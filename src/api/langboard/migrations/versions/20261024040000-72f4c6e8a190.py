"""Fence wiki editor lineages without changing historical source."""

import sqlalchemy as sa
from alembic import op


revision = "72f4c6e8a190"
down_revision = "15b6f7c83a90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("project_wiki", sa.Column("editor_generation", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("LOCK TABLE project_wiki IN ACCESS EXCLUSIVE MODE")
    if connection.scalar(sa.text("SELECT count(*) FROM project_wiki WHERE editor_generation <> 0")):
        raise RuntimeError("Used wiki lineage protection cannot be dropped")
    op.drop_column("project_wiki", "editor_generation")
