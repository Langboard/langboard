"""Retain private Vault-backed protection across secret material retirement.

Existing references have no historical backfill: only material still available
can be seeded by native lifecycle operations before it is retired.
"""

import sqlalchemy as sa
from alembic import op


revision = "cdef901b2345"
down_revision = "bdef890a1234"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("secret_reference", sa.Column("protection_locator", sa.Text(), nullable=True))


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM secret_reference WHERE protection_locator IS NOT NULL LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Cannot discard retained secret name protection")
    with op.batch_alter_table("secret_reference") as batch:
        batch.drop_column("protection_locator")
