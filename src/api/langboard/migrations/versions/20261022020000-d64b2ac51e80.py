"""Keep continuous compaction cadence and claims across worker restarts."""

import sqlalchemy as sa
from alembic import op


revision = "d64b2ac51e80"
down_revision = "b4a719de028c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_compaction_schedule",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("card_id", sa.BigInteger(), nullable=False),
        sa.Column("profile_id", sa.BigInteger(), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(32), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(32), nullable=True),
    )
    op.create_index("ix_workflow_compaction_schedule_identity", "workflow_compaction_schedule", ["card_id", "profile_id"], unique=True)
    op.create_index("ix_workflow_compaction_schedule_due", "workflow_compaction_schedule", ["next_check_at"])
    op.create_index("ix_workflow_compaction_schedule_active", "workflow_compaction_schedule", ["profile_id", "lease_expires_at"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM workflow_compaction_schedule LIMIT 1")).first():
        raise RuntimeError("Cannot discard continuous compaction schedule records")
    op.drop_table("workflow_compaction_schedule")
