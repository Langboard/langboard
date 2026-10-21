"""Retain committed workflow entry effect evidence without modifying old work."""

import sqlalchemy as sa
from alembic import op


revision = "e7b942cd815f"
down_revision = "d6e821bc704a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_effect_run",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage_id", sa.BigInteger(), nullable=False),
        sa.Column("stage_key", sa.String(), nullable=False),
        sa.Column("stage_revision", sa.String(64), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("card_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_type", sa.String(8), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("effects", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.CheckConstraint("duration_ms >= 0", name="workflow_effect_duration"),
        sa.CheckConstraint("actor_type IN ('user','bot')", name="workflow_effect_actor"),
    )
    op.create_index("ix_workflow_effect_run_stage_page", "workflow_effect_run", ["stage_id", "id"])
    for column in ("project_id", "card_id"):
        op.create_index(f"ix_workflow_effect_run_{column}", "workflow_effect_run", [column])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM workflow_effect_run LIMIT 1")).first():
        raise RuntimeError("Cannot discard workflow effect evidence")
    op.drop_table("workflow_effect_run")
