"""Local directory revisions and private durable audit; preserve all source rows."""

import sqlalchemy as sa
from alembic import op


revision = "15b6f7c83a90"
down_revision = "f0254a74d298"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scim_group", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.create_table(
        "group_directory_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("actor_uid", sa.String(), nullable=False),
        sa.Column("group_uid", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("change_facts", sa.JSON(), nullable=False),
    )
    op.create_index("ix_group_directory_audit_group_uid", "group_directory_audit", ["group_uid"])


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("LOCK TABLE group_directory_audit IN ACCESS EXCLUSIVE MODE")
    if connection.scalar(sa.text("SELECT count(*) FROM group_directory_audit")):
        raise RuntimeError("Directory audit history requires explicit retention review before downgrade")
    op.drop_table("group_directory_audit")
    op.drop_column("scim_group", "revision")
