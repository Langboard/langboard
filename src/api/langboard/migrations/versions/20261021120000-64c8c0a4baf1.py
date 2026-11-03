"""Retain one-time bot schedule admission attempts and fenced retry state."""

import sqlalchemy as sa
from alembic import op


revision = "64c8c0a4baf1"
down_revision = "0b7a16ef037b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_schedule_run",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schedule_id", sa.BigInteger(), nullable=False),
        sa.Column("scope_table", sa.String(64), nullable=False),
        sa.Column("scope_id", sa.BigInteger(), nullable=False),
        sa.Column("occurrence_key", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(32), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.CheckConstraint(
            "attempt >= 0 AND max_attempts BETWEEN 1 AND 10 AND attempt <= max_attempts",
            name="bot_schedule_run_attempts",
        ),
        sa.CheckConstraint(
            "state IN ('pending','dispatching','accepted','failed','uncertain','cancelled')",
            name="bot_schedule_run_state",
        ),
        sa.CheckConstraint(
            "(state = 'dispatching' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'dispatching' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="bot_schedule_run_lease",
        ),
    )
    op.create_index("ix_bot_schedule_run_occurrence", "bot_schedule_run", ["occurrence_key"], unique=True)
    op.create_index("ix_bot_schedule_run_due", "bot_schedule_run", ["state", "next_attempt_at"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM bot_schedule_run LIMIT 1")).first():
        raise RuntimeError("Cannot discard bot schedule admission evidence")
    op.drop_table("bot_schedule_run")
