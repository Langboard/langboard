"""Retain canonical immutable approval execution plans."""

import sqlalchemy as sa
from alembic import op


revision = "6efa789b0123"
down_revision = "5def678a9012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("graph_approval_request", sa.Column("execution_plan", sa.JSON(), nullable=True))


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("LOCK TABLE graph_approval_request IN ACCESS EXCLUSIVE MODE")
    if connection.scalar(sa.text("SELECT count(*) FROM graph_approval_request WHERE execution_plan IS NOT NULL")):
        raise RuntimeError("Cannot discard retained approval execution plans")
    op.drop_column("graph_approval_request", "execution_plan")
