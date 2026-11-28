"""Persist current-stage native hook policy selections."""

import json
import sqlalchemy as sa
from alembic import op


revision = "9bcd012e3456"
down_revision = "8abc901d2345"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workflow_stage_definition", sa.Column("policy_bots", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("LOCK TABLE workflow_stage_definition IN ACCESS EXCLUSIVE MODE")
    rows = connection.execute(sa.text("SELECT policy_bots FROM workflow_stage_definition"))
    if any((json.loads(value) if isinstance(value, str) else value) != [] for value, in rows):
        raise RuntimeError("Cannot discard retained workflow policy bot bindings")
    op.drop_column("workflow_stage_definition", "policy_bots")
