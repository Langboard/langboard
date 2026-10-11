"""Fence resource selection consent to its current connection identity."""

import sqlalchemy as sa
from alembic import op


revision = "e1b6c84f312a"
down_revision = "d9a5b73e201f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "board_app_binding", sa.Column("resource_selection_revision", sa.Integer(), server_default="0", nullable=False)
    )
    # Existing selections have no identity proof. Preserve receipts without
    # inventing consent to whichever external account is currently connected.
    op.add_column("app_resource_binding", sa.Column("identity_hash", sa.String(), server_default="", nullable=False))
    op.execute(
        "UPDATE app_resource_binding SET is_selected = false, access_state = 'revoked', health = 'unknown', access_revision = access_revision + 1"
    )
    op.alter_column("app_resource_binding", "identity_hash", server_default=None)


def downgrade():
    op.drop_column("app_resource_binding", "identity_hash")
    op.drop_column("board_app_binding", "resource_selection_revision")
