"""Record observed workflow entry without inventing timestamps for existing cards."""

import sqlalchemy as sa
from alembic import op


revision = "e75c3bd62f91"
down_revision = "d64b2ac51e80"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("card", sa.Column("workflow_stage_entered_key", sa.String(), nullable=True))
    op.add_column("card", sa.Column("workflow_stage_entered_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM card WHERE workflow_stage_entered_key IS NOT NULL "
        "OR workflow_stage_entered_at IS NOT NULL LIMIT 1"
    )).first():
        raise RuntimeError("Cannot discard observed workflow entry evidence")
    op.drop_column("card", "workflow_stage_entered_at")
    op.drop_column("card", "workflow_stage_entered_key")
