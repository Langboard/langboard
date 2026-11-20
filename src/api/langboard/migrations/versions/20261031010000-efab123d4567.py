"""Persist independent native card priority with a legacy P2 default."""

import sqlalchemy as sa
from alembic import op


revision = "efab123d4567"
down_revision = "defa012c3456"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("card") as batch:
        batch.add_column(sa.Column("priority", sa.String(), nullable=False, server_default="P2"))
        batch.create_check_constraint("ck_card_priority", "priority IN ('P0','P1','P2','P3')")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM card WHERE priority <> 'P2')")):
        raise RuntimeError("Cannot remove authored card priorities; explicitly restore P2 before downgrade")
    with op.batch_alter_table("card") as batch:
        batch.drop_constraint("ck_card_priority", type_="check")
        batch.drop_column("priority")
