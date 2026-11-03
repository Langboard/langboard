"""Bind optional Decision rounds to the native approval authority."""

import sqlalchemy as sa
from alembic import op


revision = "8abc901d2345"
down_revision = "7fab890c1234"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("graph_approval_request") as batch:
        batch.add_column(sa.Column("decision_case_id", sa.BigInteger(), nullable=True))
        batch.create_foreign_key("fk_graph_approval_decision", "decision_case", ["decision_case_id"], ["id"])
        batch.create_unique_constraint("uq_graph_approval_decision", ["decision_case_id"])


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("LOCK TABLE graph_approval_request IN ACCESS EXCLUSIVE MODE")
    if connection.scalar(sa.text("SELECT count(*) FROM graph_approval_request WHERE decision_case_id IS NOT NULL")):
        raise RuntimeError("Cannot discard retained native approval policy bindings")
    with op.batch_alter_table("graph_approval_request") as batch:
        batch.drop_constraint("uq_graph_approval_decision", type_="unique")
        batch.drop_constraint("fk_graph_approval_decision", type_="foreignkey")
        batch.drop_column("decision_case_id")
