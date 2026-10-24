"""Add optional workflow entry bot bindings without rewriting existing policies."""

import json
import sqlalchemy as sa
from alembic import op


revision = "a1b2c3d4e5f6"
down_revision = "f86d4ce73a02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workflow_stage_definition", sa.Column("entry_bots", sa.JSON(), nullable=False, server_default="[]"))


def downgrade():
    rows = op.get_bind().execute(sa.text("SELECT entry_bots, continuous_effects FROM workflow_stage_definition"))
    for entry, continuous in rows:
        entry = json.loads(entry) if isinstance(entry, str) else entry
        continuous = json.loads(continuous) if isinstance(continuous, str) else continuous
        if entry or continuous.get("bots"):
            raise RuntimeError("Cannot discard configured workflow bot bindings")
    op.drop_column("workflow_stage_definition", "entry_bots")
