"""Retain immutable Group/Team actor creation provenance on Decision rounds."""

import sqlalchemy as sa
from alembic import op


revision = "5def678a9012"
down_revision = "4abc567def90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("decision_case", sa.Column("actor_snapshot", sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("LOCK TABLE decision_case IN ACCESS EXCLUSIVE MODE")
    if connection.scalar(sa.text("SELECT count(*) FROM decision_case WHERE actor_snapshot IS NOT NULL")):
        raise RuntimeError("Cannot discard retained Decision actor snapshots")
    # Direct DROP COLUMN avoids rebuilding a referenced parent table and therefore
    # preserves native CASCADE children on current supported SQLite/PostgreSQL.
    op.drop_column("decision_case", "actor_snapshot")
