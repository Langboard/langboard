"""Persist opt-in continuous workflow policies without changing existing entry effects."""

import json
import sqlalchemy as sa
from alembic import op


revision = "d6e821bc704a"
down_revision = "c4a8f16d3092"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workflow_stage_definition", sa.Column(
        "continuous_effects", sa.JSON(), nullable=False, server_default=sa.text("'{}'"),
    ))


def downgrade():
    for value in op.get_bind().execute(sa.text("SELECT continuous_effects FROM workflow_stage_definition")).scalars():
        policy = json.loads(value) if isinstance(value, str) else value
        if policy:
            raise RuntimeError("Cannot discard saved continuous workflow policies")
    op.drop_column("workflow_stage_definition", "continuous_effects")
