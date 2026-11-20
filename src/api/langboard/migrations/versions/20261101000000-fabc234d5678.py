"""Keep user JWT revocation durable across account reactivation."""

import sqlalchemy as sa
from alembic import op


revision = "fabc234d5678"
down_revision = "efab123d4567"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch:
        batch.add_column(sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"))
        batch.create_check_constraint("ck_user_auth_version", "auth_version >= 0")


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM "user" WHERE auth_version <> 0)')):
        raise RuntimeError("Cannot remove persisted credential revocations")
    with op.batch_alter_table("user") as batch:
        batch.drop_constraint("ck_user_auth_version", type_="check")
        batch.drop_column("auth_version")
