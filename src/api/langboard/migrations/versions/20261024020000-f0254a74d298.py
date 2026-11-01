"""Enforce the instance active tenant invariant without inventing tenant data.

Data bootstrap and nullable project bindings require the separately reviewed
InstanceTenantCutoverCommand. This migration never selects a name or owner.
"""

import sqlalchemy as sa
from alembic import op


revision = "f0254a74d298"
down_revision = "e9143f63c187"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name not in ("sqlite", "postgresql"):
        raise RuntimeError("Singleton tenant migration supports SQLite and PostgreSQL")
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("LOCK TABLE organization IN SHARE ROW EXCLUSIVE MODE")
    if connection.scalar(sa.text("SELECT count(*) FROM organization")) > 1:
        raise RuntimeError("Multiple organizations, including inactive records, require manual review")
    op.create_index(
        "uq_organization_single_active",
        "organization",
        ["is_active"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
        postgresql_where=sa.text("is_active"),
    )


def downgrade():
    op.drop_index("uq_organization_single_active", table_name="organization")
