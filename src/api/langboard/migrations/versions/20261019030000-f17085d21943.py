"""Keep legacy user audits while admitting explicit native workload principals."""

import sqlalchemy as sa
from alembic import op


revision = "f17085d21943"
down_revision = "e06f74c10832"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("secret_reference") as batch:
        batch.drop_constraint("ck_secret_reference_scope", type_="check")
        batch.create_check_constraint(
            "ck_secret_reference_scope",
            "scope IN ('personal','project','workspace','instance') AND (scope != 'instance' OR scope_id = 0)",
        )
    with op.batch_alter_table("secret_reference_audit") as batch:
        batch.alter_column("actor_id", existing_type=sa.BigInteger(), nullable=True)
        batch.add_column(sa.Column("actor_kind", sa.String(), nullable=False, server_default="user"))
        batch.add_column(sa.Column("principal_id", sa.String(), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM secret_reference_audit WHERE actor_id IS NULL LIMIT 1")).first():
        raise RuntimeError("Cannot discard workload audit identity")
    if op.get_bind().execute(sa.text("SELECT 1 FROM secret_reference WHERE scope = 'instance' LIMIT 1")).first():
        raise RuntimeError("Cannot discard instance secret scope")
    with op.batch_alter_table("secret_reference") as batch:
        batch.drop_constraint("ck_secret_reference_scope", type_="check")
        batch.create_check_constraint("ck_secret_reference_scope", "scope IN ('personal','project','workspace')")
    with op.batch_alter_table("secret_reference_audit") as batch:
        batch.drop_column("principal_id")
        batch.drop_column("actor_kind")
        batch.alter_column("actor_id", existing_type=sa.BigInteger(), nullable=False)
