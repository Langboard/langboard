"""Retain profile and attempt evidence for workflow compaction runs."""

import sqlalchemy as sa
from alembic import op


revision = "f9c053de926a"
down_revision = "e7b942cd815f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workflow_effect_run", sa.Column("execution_key", sa.String(128), nullable=True))
    op.add_column("workflow_effect_run", sa.Column("profile_id", sa.BigInteger(), nullable=True))
    op.add_column("workflow_effect_run", sa.Column("profile_name", sa.String(255), nullable=True))
    op.add_column("workflow_effect_run", sa.Column("error_code", sa.String(64), nullable=True))
    op.add_column("workflow_effect_run", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_workflow_effect_run_execution_key", "workflow_effect_run", ["execution_key"], unique=True)


def downgrade():
    count = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM workflow_effect_run WHERE execution_key IS NOT NULL OR profile_id IS NOT NULL "
        "OR profile_name IS NOT NULL OR error_code IS NOT NULL OR lease_expires_at IS NOT NULL"
    )).scalar_one()
    if count:
        raise RuntimeError("Cannot discard workflow execution evidence")
    op.drop_index("ix_workflow_effect_run_execution_key", table_name="workflow_effect_run")
    for column in ("lease_expires_at", "error_code", "profile_name", "profile_id", "execution_key"):
        op.drop_column("workflow_effect_run", column)
