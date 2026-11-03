"""Add optional authored card next action, preserving legacy unset values."""

import sqlalchemy as sa
from alembic import op


revision = "defa012c3456"
down_revision = "cdef901b2345"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("card") as batch:
        batch.add_column(sa.Column("next_action", sa.Text(), nullable=True))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM card WHERE next_action IS NOT NULL)")):
        raise RuntimeError("Cannot remove authored next actions; explicitly clear them before downgrade")
    with op.batch_alter_table("card") as batch:
        batch.drop_column("next_action")
